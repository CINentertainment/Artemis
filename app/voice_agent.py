"""User-session voice listener. Microphone audio stays in memory; endpoints are loopback."""
import io,json,re,time,queue,threading,collections,os
from pathlib import Path
from uuid import uuid4
from app.services.voice_hub import STATE,atomic,read_json,settings
BASE='http://127.0.0.1:8200'
def response_error(response):
    if response.is_success:return
    try:detail=response.json().get('detail',response.text[:400])
    except Exception:detail=response.text[:400]
    raise RuntimeError('HTTP '+str(response.status_code)+': '+str(detail)[:500])
def remove_wake(text,phrase):
    words=[re.escape(w)for w in phrase.split()]
    return re.sub(r'^\W*'+r'\W+'.join(words)+r'\W*','',text,flags=re.I).strip()
def known_command(text):
    clean=re.sub(r'[^a-z ]',' ',text.casefold());clean=re.sub(r'\s+',' ',clean).strip()
    return clean.startswith('open mission ')or clean in {'list missions','mission status','brief this mission','pause this mission','resume this mission','prioritize this mission','repeat that','repeat last reply','speak faster','speak slower','normal speaking speed','brief answers','full answers','sleep for ten minutes','backup status','specialist team status','system status','status','what are you doing','read my brief','read the latest brief','read latest brief','give me my daily brief','brief me','prepare a new brief','create a new brief','prepare my daily brief','what changed','what changed overnight','show changes','pause market monitoring','resume market monitoring','pause market research','resume market research','pause daily briefing','resume daily briefing','stop','stop speaking','stop briefing','pause briefing','pause speaking','resume briefing','resume speaking','mute microphone','stop listening','cancel','cancel research','confirm research','confirm investigation','start voice session','new voice session','start conversation','new conversation','end conversation','end voice session','look into that further','investigate that','research that','what did you do today','daily activity recap','operations recap','keep monitoring this','pause monitoring this','what changed with this event','whats changed with this event','event status','what needs my attention','prioritize this event','read my alerts','brief me on what changed','snooze notifications','resume notifications','start briefing session'}

def wake_match(text,phrase):
    clean=re.sub(r'\s+',' ',re.sub(r'[^a-z ]',' ',text.casefold())).strip()
    return bool(re.search(r'\b'+re.escape(phrase)+r'\b',clean))
def chunks(text,limit=600):
    words=text.split();out=[];current=''
    for word in words:
        if len(current)+len(word)+1>limit and current:out.append(current);current=''
        current+=((' 'if current else'')+word)
    if current:out.append(current)
    return out

def instance_lock():
    STATE.mkdir(parents=True,exist_ok=True);handle=(STATE/'agent.lock').open('a+b');handle.seek(0);handle.write(b'0');handle.flush();handle.seek(0)
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    return handle
class Player:
    def __init__(self,agent):
        self.agent=agent;self.guard=threading.RLock();self.deliveries={};self.version=0;self.paused=False;self.active=False;self.stage='idle';self.pending=queue.Queue();threading.Thread(target=self.loop,daemon=True).start()
    def report(self,version,state):
        delivery=self.deliveries.get(version)
        if delivery and getattr(self.agent,'delivery_client',None):self.agent.delivery_client.event(delivery,state)
        if state in {'completed','failed','cancelled','deferred'}:self.deliveries.pop(version,None)
    def say(self,text,delivery=None,only_if_idle=False):
        from app.services.voice_timing import speech_chunks
        limit=settings().get('reply_limit',12000)
        if len(text)>limit:text=text[:limit].rsplit(' ',1)[0]+'. Spoken reading shortened. Open Artemis for the full content.'
        with self.guard:
            if only_if_idle and(self.active or self.agent.busy or self.agent.muted or self.agent.confirmation or self.agent.phase!='wake'):return False
            self.report(self.version,'cancelled');self.version+=1;self.paused=False;self.active=True
            if delivery:
                self.deliveries[self.version]=delivery
                if getattr(self.agent,'delivery_client',None):self.agent.delivery_client.event(delivery,'leased')
            self.agent.reply_started=time.monotonic();self.agent.metrics.pop('first_audio_seconds',None)
            self.pending.put((self.version,speech_chunks(text)));return True
    def stop(self):
        with self.guard:self.report(self.version,'cancelled');self.version+=1;self.paused=False;self.active=False;self.stage='idle'
    def loop(self):
        import httpx,soundfile as sf,sounddevice as sd
        while not self.agent.quit.is_set():
            try:version,parts=self.pending.get(timeout=.2)
            except queue.Empty:continue
            if version!=self.version:continue
            ready=queue.Queue(maxsize=2)
            def put(value,token=version,target=ready):
                while token==self.version and not self.agent.quit.is_set():
                    try:target.put(value,timeout=.1);return
                    except queue.Full:pass
            def produce(token=version,text_parts=tuple(parts),deliver=put):
                try:
                    with httpx.Client(timeout=httpx.Timeout(130,connect=3),trust_env=False)as client:
                        for index,text in enumerate(text_parts):
                            if token!=self.version:return
                            if index==0:self.stage='synthesizing';self.agent.set_stage('synthesizing','Preparing first audio; the local speech model stays loaded between replies.')
                            for attempt in range(8):
                                response=client.post(BASE+'/voice/speak',json={'text':text})
                                if response.status_code==422 and 'Another spoken reply' in response.text and attempt<7:
                                    if self.agent.quit.wait(.2)or token!=self.version:return
                                    continue
                                response_error(response);break
                            if token!=self.version:return
                            audio,rate=sf.read(io.BytesIO(response.content),dtype='float32',always_2d=True);deliver(('audio',audio,rate))
                    deliver(('done',))
                except Exception as error:deliver(('error',str(error)))
            threading.Thread(target=produce,daemon=True,name='artemis-speech-prefetch').start()
            try:
                first=True;reported_start=False
                while version==self.version and not self.agent.quit.is_set():
                    try:item=ready.get(timeout=.1)
                    except queue.Empty:continue
                    if item[0]=='done':break
                    if item[0]=='error':raise RuntimeError(item[1])
                    _,samples,rate=item
                    if version!=self.version:break
                    delivery=self.deliveries.get(version)
                    if first and delivery and getattr(self.agent,'delivery_client',None)and not self.agent.delivery_client.authorize(delivery):
                        self.report(version,'deferred');break
                    if first:self.agent.metrics['first_audio_seconds']=round(time.monotonic()-self.agent.reply_started,2);first=False
                    self.stage='playing';self.agent.set_stage('playing','Playing reply. Next speech chunks are prepared in the background.')
                    with sd.OutputStream(samplerate=rate,channels=samples.shape[1],dtype='float32',device=settings().get('output_device'))as output:
                        for start in range(0,len(samples),1024):
                            while self.paused and version==self.version and not self.agent.quit.wait(.05):pass
                            if version!=self.version or self.agent.quit.is_set():break
                            output.write(samples[start:start+1024])
                            if not reported_start:self.report(version,'started');reported_start=True
                if version==self.version:self.report(version,'completed');self.active=False;self.stage='idle';self.agent.set_stage('wake','Reply finished. Ready for the wake phrase or Listen now.')
            except Exception as error:
                if version==self.version:self.report(version,'failed');self.active=False;self.stage='error';self.agent.error='Speech output failed: '+str(error);self.agent.set_stage('error',self.agent.error)
class Agent:
    def __init__(self):
        self.client_id=str(uuid4());self.delivery_client=None;self.quit=threading.Event();self.muted=False;self.busy=False;self.phase='starting';self.message='Loading local wake-phrase model…';self.confirmation=None;self.player=None;self.level=0;self.last_text='';self.control_until=0;self.capture_open=False;self.started=time.time();self.stage_started=time.monotonic();self.error='';self.last_wake='';self.last_reply='';self.full_reply='';self.sleep_until=0;self.force_capture=False;self.capture_requires_wake=True;self.metrics={};self.reply_started=0;self.calibration_until=0;self.calibration_levels=[];self.noise_floor=0;self.ack_until=0;self.partial_text='';self.partial_since=0;self.last_partial='';self.command_seen=False
    def warmup(self):
        self.metrics['warmup']='warming up'
        def work():
            try:
                import httpx
                started=time.monotonic()
                with httpx.Client(timeout=150,trust_env=False)as client:r=client.post(BASE+'/voice-hub/warmup');response_error(r)
                self.metrics['warmup_seconds']=round(time.monotonic()-started,2);self.metrics['warmup']='ready'
            except Exception as error:self.metrics['warmup']='failed: '+str(error)[:200]
        threading.Thread(target=work,daemon=True).start()
    def acknowledge(self):
        if not settings().get('acknowledgement',True)or self.player.active:return
        def tone():
            try:
                import numpy as np,sounddevice as sd
                rate=24000;samples=(.025*np.sin(2*np.pi*880*np.arange(1440)/rate)).astype('float32')
                with sd.OutputStream(samplerate=rate,channels=1,device=settings().get('output_device'))as output:output.write(samples.reshape(-1,1))
            except Exception:pass
        self.ack_until=time.monotonic()+.12;threading.Thread(target=tone,daemon=True).start()
    def set_stage(self,phase,message):
        self.phase=phase;self.message=message;self.stage_started=time.monotonic()
    def apply(self,action,payload=None):
        payload=payload or {}
        if action=='repeat':
            self.player.say(self.full_reply or 'No prior reply is available in this listener session.')
        if action=='sleep':
            self.sleep_until=time.time()+600;self.muted=True;self.player.stop();self.message='Microphone paused for ten minutes.'
        if action=='calibrate':
            self.muted=False;self.player.stop();self.force_capture=False;self.confirmation=None;self.calibration_levels=[];self.calibration_until=time.monotonic()+3;self.error='';self.set_stage('calibrating','Stay quiet for three seconds while microphone noise is measured.')
        if action=='listen_once':
            self.muted=False;self.player.stop();self.force_capture=True;self.error='';self.acknowledge();self.set_stage('ready','Listening for one command without a wake phrase...')
        if action=='reply':
            self.confirmation=payload.get('confirmation');self.last_reply=payload.get('speech','')[:500];self.full_reply=payload.get('speech','')[:12000];self.player.say(payload.get('speech',''),delivery=payload.get('delivery'))
        if action=='speaker_test':
            self.last_reply='Artemis voice output is working.';self.player.say(self.last_reply)
        if action=='mute':self.sleep_until=0;self.muted=True;self.message='Microphone muted.'
        if action=='unmute':self.sleep_until=0;self.muted=False;self.message='Wake listening enabled.'
        if action=='pause':self.player.paused=True;self.message='Briefing paused.'
        if action=='resume':self.player.paused=False;self.message='Briefing resumed.'
        if action=='stop':self.player.stop();self.confirmation=None;self.set_stage('wake','Speech stopped.')
        if action=='quit':self.quit.set();self.player.stop()
    def poll_controls(self):
        if self.sleep_until and time.time()>=self.sleep_until:self.sleep_until=0;self.muted=False;self.message='Microphone pause ended.'
        folder=STATE/'controls'
        for path in sorted(folder.glob('*.json'),key=lambda p:p.stat().st_mtime):
            value=read_json(path,{})
            if self.started<=value.get('created',0)and time.time()-value.get('created',0)<20:self.apply(value.get('action'),value.get('payload'))
            path.unlink(missing_ok=True)
    def route(self,text):
        import httpx
        self.last_text=text[:500]
        local={'stop':'stop','stop speaking':'stop','stop briefing':'stop','pause briefing':'pause','pause speaking':'pause','resume briefing':'resume','resume speaking':'resume','mute microphone':'mute','stop listening':'mute'}
        clean=re.sub(r'[^a-z ]','',text.casefold()).strip()
        if clean in local:self.apply(local[clean]);return
        self.set_stage('command','Sending recognized command to Artemis...');command_started=time.monotonic()
        with httpx.Client(timeout=httpx.Timeout(40,connect=3),trust_env=False)as client:
            response=client.post(BASE+'/voice-hub/command',json={'text':text[:500],'request_id':str(uuid4()),'confirmation':self.confirmation,'source':'native'});response_error(response);reply=response.json()
        if reply.get('control')in {'repeat','sleep'}:
            self.set_stage('wake','Playback control queued.');return
        self.metrics['command_seconds']=round(time.monotonic()-command_started,2);self.confirmation=reply.get('confirmation');self.last_reply=reply['speech'][:500];self.full_reply=reply['speech'][:12000]
        if reply.get('pending'):
            self.set_stage('wake',reply['speech']+' Open Voice Sessions for the transcript.');return
        if reply.get('open_path')and re.fullmatch(r'/reports/[0-9a-f-]{36}/pdf',reply['open_path']):
            import webbrowser
            webbrowser.open(BASE+reply['open_path'])
        if reply.get('delivery'):self.player.say(reply['speech'],delivery=reply['delivery'])
        else:self.player.say(reply['speech'])
    def submit_text(self,text):
        if self.busy:return
        self.busy=True;self.error=''
        def work():
            try:self.route(text)
            except Exception as error:self.error=str(error);self.set_stage('error','Command failed: '+self.error)
            finally:
                self.busy=False
                if not self.player.active and not self.error:self.set_stage('wake','Command handled.')
        threading.Thread(target=work,daemon=True).start()
    def submit(self,pcm,rate,require_wake=True):
        self.busy=True;self.error='';transcription_started=time.monotonic();self.set_stage('transcribing','Transcribing recorded command locally (up to 45 seconds)...')
        def work():
            try:
                import httpx,soundfile as sf
                stream=io.BytesIO();sf.write(stream,pcm,rate,format='WAV',subtype='PCM_16')
                with httpx.Client(timeout=httpx.Timeout(45,connect=3),trust_env=False)as client:
                    response=client.post(BASE+'/voice/transcribe',params={'fast':str(settings().get('fast_transcription',True)).lower()},files={'file':('command.wav',stream.getvalue(),'audio/wav')});response_error(response);text=response.json().get('text','')
                self.metrics['transcription_seconds']=round(time.monotonic()-transcription_started,2);self.last_text=text[:500];phrase=settings()['wake_phrase']
                if require_wake and not wake_match(text,phrase):
                    self.set_stage('wake','Wake phrase was not confirmed. Click Listen now to bypass wake detection.');return
                text=remove_wake(text,phrase)
                if not text:self.set_stage('wake','No command was transcribed. Click Listen now and speak after the panel says listening.');return
                self.route(text)
            except Exception as error:self.error=type(error).__name__+': '+str(error);self.set_stage('error','Voice request failed: '+self.error)
            finally:
                self.busy=False
                if not self.player.active and not self.error and self.phase in {'transcribing','command'}:self.set_stage('wake','Ready for another command.')
        threading.Thread(target=work,daemon=True).start()
    def listen(self):
        import numpy as np,sounddevice as sd
        from vosk import Model,KaldiRecognizer,SetLogLevel
        SetLogLevel(-1)
        try:
            model=Model(str(STATE/'models/vosk-model-small-en-us-0.15'))
            self.message='Say '+settings()['wake_phrase']+', then your command.';self.phase='wake'
            while not self.quit.is_set():
                self.poll_controls()
                if self.muted:self.phase='muted';self.quit.wait(.1);continue
                opts=settings();device=opts['device'];rate=int(sd.query_devices(device,'input')['default_samplerate']);audio=queue.Queue(maxsize=30)
                def callback(data,frames,timing,status):
                    if status:self.metrics['input_status_events']=self.metrics.get('input_status_events',0)+1
                    try:audio.put_nowait(bytes(data))
                    except queue.Full:self.metrics['input_queue_drops']=self.metrics.get('input_queue_drops',0)+1
                rec=KaldiRecognizer(model,rate);history=collections.deque(maxlen=16);was_playing=False;opts_checked=0;captured=[];capture_start=0;last_sound=0;speech=False
                with sd.RawInputStream(samplerate=rate,blocksize=max(1,int(rate*.1)),device=device,dtype='int16',channels=1,callback=callback):
                    self.capture_open=True
                    while not self.quit.is_set()and not self.muted:
                        self.poll_controls()
                        try:raw=audio.get(timeout=.2)
                        except queue.Empty:continue
                        stamp=time.monotonic()
                        if stamp-opts_checked>1:opts.update(settings());opts_checked=stamp
                        samples=np.clip(np.frombuffer(raw,dtype=np.int16).astype(np.float32)/32768*opts.get('input_gain',1),-1,1);self.level=float(np.sqrt(np.mean(samples*samples)));raw=(samples*32767).astype(np.int16).tobytes()
                        if self.calibration_until:
                            if stamp<self.calibration_until:self.calibration_levels.append(self.level);continue
                            try:
                                from app.services.voice_timing import calibrate
                                self.noise_floor,threshold=calibrate(self.calibration_levels);atomic(STATE/'settings.json',{**settings(),'energy':threshold});opts['energy']=threshold;self.set_stage('wake','Calibration saved. Speech threshold '+str(round(threshold,4)))
                            except ValueError as error:self.error=str(error);self.set_stage('error',self.error)
                            self.calibration_until=0;rec=KaldiRecognizer(model,rate);history.clear();continue
                        if stamp<self.ack_until:continue
                        if self.player.active and not opts.get('barge_in',False):
                            was_playing=True;self.phase=self.player.stage;history.clear();continue
                        if was_playing and not self.player.active:rec=KaldiRecognizer(model,rate);history.clear();was_playing=False
                        history.append(samples.copy())
                        if self.busy:continue
                        final=rec.AcceptWaveform(raw);recognized=json.loads(rec.Result()if final else rec.PartialResult()).get('text'if final else'partial','')
                        if recognized!=self.partial_text:self.partial_text=recognized;self.partial_since=stamp
                        stable=bool(recognized)and stamp-self.partial_since>=.25
                        # Barge-in only accepts explicit wake + playback controls during speech.
                        if self.player.active:
                            was_playing=True;self.phase=self.player.stage
                            if wake_match(recognized,opts['wake_phrase']):self.control_until=time.monotonic()+8
                            if time.monotonic()<self.control_until and(final or stable):
                                tail=recognized.split(opts['wake_phrase'])[-1].strip()
                                for word,action in [('stop','stop'),('pause','pause'),('resume','resume')]:
                                    if tail in (word,word+' briefing',word+' speaking'):self.apply(action);self.control_until=0;rec=KaldiRecognizer(model,rate);break
                            continue
                        if self.force_capture:
                            self.force_capture=False;self.phase='capture';captured=[];capture_start=time.monotonic();last_sound=capture_start;speech=False;self.capture_requires_wake=False;self.command_seen=False;self.partial_text='';self.last_partial='';rec=KaldiRecognizer(model,rate);self.message='Listening now. Say your command.'
                        if(final or(stable and self.level<opts['energy']))and recognized and not self.busy:
                            phrase=opts['wake_phrase'];verified=wake_match(recognized,phrase)
                            candidate=remove_wake(recognized,phrase)if verified else recognized
                            if known_command(candidate)and(verified or(self.phase=='capture'and not self.capture_requires_wake)):
                                self.last_wake=recognized if verified else'Listen now / armed command';self.metrics['route']='local fixed command';self.acknowledge();self.submit_text(candidate);captured=[];history.clear();rec=KaldiRecognizer(model,rate);continue
                        if self.phase!='capture':
                            if self.confirmation or wake_match(recognized,opts['wake_phrase'])or(opts['wake_phrase'].startswith('hey ')and re.search(r'\bhey\b',recognized)):
                                self.phase='capture';self.capture_requires_wake=not bool(self.confirmation)and not wake_match(recognized,opts['wake_phrase']);self.last_wake=recognized;self.command_seen=False;self.partial_text='';self.last_partial='';self.acknowledge()if not self.capture_requires_wake else None;captured=[]if self.confirmation else list(history);capture_start=time.monotonic();last_sound=capture_start;speech=bool(captured);self.message='Wake heard. Say your command; silence ends the recording.'
                            else:
                                if self.phase not in {'error','transcribing','command','synthesizing','playing'}:self.phase='wake'
                                continue
                        captured.append(samples.copy());stamp=time.monotonic()
                        if recognized and recognized!=self.last_partial:
                            self.last_partial=recognized;last_sound=stamp;speech=True
                            if wake_match(recognized,opts['wake_phrase']):
                                self.capture_requires_wake=False;self.command_seen=bool(remove_wake(recognized,opts['wake_phrase']))
                            elif not self.capture_requires_wake:self.command_seen=True
                        if self.level>opts['energy']:last_sound=stamp;speech=True
                        from app.services.voice_timing import end_ready
                        if end_ready(stamp-capture_start,stamp-last_sound,speech,self.command_seen,opts.get('end_silence',.55)):
                            self.phase='wake'
                            if speech:self.metrics['route']='Whisper fallback';self.submit(np.concatenate(captured),rate,require_wake=self.capture_requires_wake)
                            else:self.confirmation=None;self.message='No command heard. Say the wake phrase to try again.'
                            captured=[];rec=KaldiRecognizer(model,rate);history.clear()
                self.capture_open=False
        except Exception as error:self.capture_open=False;self.phase='error';self.message='Listener error: '+str(error)[:250]+'. Check setup, input device and Windows microphone permissions.'
    def gui(self):
        import tkinter as tk
        from app.services.notification_client import DeliveryClient
        self.delivery_client=DeliveryClient(self)
        root=tk.Tk();root.title('Artemis Voice Control');root.geometry('650x650');root.configure(bg='#14101d')
        label=tk.Label(root,text='',wraplength=490,bg='#14101d',fg='#eee2ff',font=('Segoe UI',12));label.pack(padx=18,pady=18)
        frame=tk.Frame(root,bg='#14101d');frame.pack()
        for name,action in [('Calibrate microphone','calibrate'),('Listen now','listen_once'),('Test speaker','speaker_test'),('Mute microphone','mute'),('Unmute','unmute'),('Pause brief','pause'),('Resume','resume'),('Stop speech','stop'),('Repeat last reply','repeat')]:tk.Button(frame,text=name,command=lambda a=action:self.apply(a),bg='#3b2852',fg='white').pack(pady=3,fill='x')
        from tkinter import ttk
        import webbrowser
        tk.Button(root,text='Open briefings & alerts',command=lambda:webbrowser.open(BASE+'/static/inbox.html'),bg='#49315f',fg='white').pack(pady=6)
        meter=ttk.Progressbar(root,maximum=.1,length=480);meter.pack(pady=8)
        def update():
            if self.quit.is_set():
                atomic(STATE/'status.json',dict(heartbeat=time.time(),phase='stopped',microphone_active=False,message='Voice listener stopped.'));root.destroy();return
            self.delivery_client.process(root)
            value=dict(client_id=self.client_id,playback_active=self.player.active,command_busy=self.busy,awaiting_confirmation=bool(self.confirmation),muted=self.muted,heartbeat=time.time(),phase='muted'if self.muted else self.phase,microphone_active=self.capture_open and not self.muted,message=self.message,level=round(self.level,4),paused=self.player.paused,last_wake=self.last_wake,last_transcript=self.last_text,last_reply=self.last_reply,error=self.error,stage_seconds=round(time.monotonic()-self.stage_started,1),noise_floor=round(self.noise_floor,5),timings=dict(self.metrics))
            meter['value']=min(.1,self.level);atomic(STATE/'status.json',value);label.configure(text=value['phase'].upper()+'\n'+self.message+'\nHeard: '+self.last_text[:120]);root.after(500,update)
        root.protocol('WM_DELETE_WINDOW',lambda:self.apply('quit'));update();root.mainloop();self.quit.set()
def main():
    import sys
    if '--devices'in sys.argv:
        import sounddevice as sd
        print(sd.query_devices());return
    try:
        import sounddevice,soundfile,numpy,vosk,httpx
    except Exception as error:
        atomic(STATE/'status.json',dict(heartbeat=time.time(),phase='error',microphone_active=False,message='Run Setup-Voice-Activation.ps1: '+str(error)));return
    lock=instance_lock();agent=Agent();agent.player=Player(agent);agent.warmup();threading.Thread(target=agent.listen,daemon=True).start();agent.gui();lock.close()
if __name__=='__main__':main()
