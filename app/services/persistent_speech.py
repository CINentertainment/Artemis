"""Serialize speech requests to one CPU worker. Restart after timeout or exit."""
import atexit,base64,json,os,queue,subprocess,sys,threading
from pathlib import Path
from uuid import uuid4
class SpeechProcess:
    def __init__(self):self.lock=threading.RLock();self.process=None;self.responses=None;self.starts=0
    def close(self):
        with self.lock:
            process=self.process;self.process=None
            if process:
                try:process.stdin.close()
                except OSError:pass
                try:process.terminate();process.wait(timeout=3)
                except (OSError,subprocess.TimeoutExpired):
                    try:process.kill();process.wait(timeout=3)
                    except (OSError,subprocess.TimeoutExpired):pass
                for pipe in (process.stdout,process.stderr):
                    if pipe:
                        try:pipe.close()
                        except OSError:pass
    def start(self):
        if self.process and self.process.poll()is None:return
        self.close();env=os.environ.copy();env['OMP_NUM_THREADS']='2';env['MKL_NUM_THREADS']='2'
        process=subprocess.Popen([sys.executable,'-u','-m','app.services.speech_engine'],cwd=Path(__file__).resolve().parents[2],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt'else 0)
        responses=queue.Queue(maxsize=4);self.responses=responses;self.process=process;self.starts+=1
        def read():
            try:
                for raw in process.stdout:
                    if len(raw)>30*1024*1024:break
                    try:value=json.loads(raw)
                    except ValueError:continue
                    try:responses.put(value,timeout=1)
                    except queue.Full:break
            except (OSError,ValueError):pass
            finally:
                try:responses.put({'error':'Speech worker exited unexpectedly.'},timeout=1)
                except queue.Full:pass
        threading.Thread(target=read,daemon=True,name='artemis-speech-response').start()
    def speak(self,text,model_path,voices_path,timeout=120,speed=1.0):
        with self.lock:
            self.start();key=str(uuid4());payload={'id':key,'text':text,'model_path':str(model_path),'voices_path':str(voices_path),'speed':min(1.4,max(.75,float(speed)))}
            try:
                self.process.stdin.write((json.dumps(payload)+'\n').encode());self.process.stdin.flush()
                result=self.responses.get(timeout=timeout)
                if result.get('error'):raise RuntimeError(result['error'])
                if result.get('id')!=key:raise RuntimeError('Speech worker response did not match its request.')
                content=base64.b64decode(result['audio'],validate=True)
                if len(content)<=44 or not content.startswith(b'RIFF'):raise RuntimeError('Speech worker returned invalid WAV audio.')
                return content
            except Exception:
                self.close();raise
engine=SpeechProcess();atexit.register(engine.close)
