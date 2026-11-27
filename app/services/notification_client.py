"""Native local inbox delivery. No audio is saved; UI work stays on Tk's thread."""
import queue,threading,time,webbrowser
from uuid import uuid4
BASE='http://127.0.0.1:8200'
class DeliveryClient:
    def __init__(self,agent):
        self.agent=agent;self.pending=queue.Queue();self.receipts=queue.Queue();self.active={};self.lock=threading.RLock();self.last_poll=0;self.last_heartbeat=0
        threading.Thread(target=self.loop,daemon=True,name='artemis-notification-client').start()
    def event(self,delivery,state):
        if not delivery:return
        with self.lock:
            if state in {'leased','started'}:self.active[delivery['id']]=delivery
            elif state!='heartbeat':self.active.pop(delivery['id'],None)
        if state!='leased':self.receipts.put((delivery,state,0))
    def idle(self):
        a=self.agent
        return bool(a.player and not a.player.active and not a.player.paused and not a.busy and not a.muted and not a.confirmation and a.phase=='wake'and time.monotonic()-a.stage_started>=15)
    def loop(self):
        import httpx
        with httpx.Client(timeout=httpx.Timeout(2,connect=1),trust_env=False)as client:
            while not self.agent.quit.is_set():
                try:
                    delivery,state,tries=self.receipts.get_nowait()
                    body={'client_id':delivery['client_id'],'token':delivery['token'],'state':state}
                    try:
                        r=client.post(BASE+'/delivery/attempts/'+delivery['id']+'/receipt',json=body);r.raise_for_status()
                    except Exception:
                        if tries<2:self.receipts.put((delivery,state,tries+1))
                except queue.Empty:pass
                if time.monotonic()-self.last_heartbeat>=5:
                    self.last_heartbeat=time.monotonic()
                    with self.lock:active=list(self.active.values())
                    for delivery in active:self.event(delivery,'heartbeat')
                if self.idle()and time.monotonic()-self.last_poll>=5:
                    self.last_poll=time.monotonic()
                    for channel in ['desktop','speech']:
                        try:
                            r=client.post(BASE+'/delivery/native/lease',json={'client_id':self.agent.client_id,'channel':channel});r.raise_for_status();delivery=r.json()
                            if delivery:self.event(delivery,'leased');self.pending.put(delivery)
                        except Exception:pass
                self.agent.quit.wait(.25)
    def authorize(self,delivery):
        if delivery.get('channel')=='briefing':return True
        import httpx
        try:
            with httpx.Client(timeout=.75,trust_env=False)as client:
                response=client.post(BASE+'/delivery/attempts/'+delivery['id']+'/authorize',json={'client_id':delivery['client_id'],'token':delivery['token'],'state':'heartbeat'})
                return response.is_success and response.json().get('allowed')is True
        except Exception:return False
    def process(self,root):
        import tkinter as tk
        try:delivery=self.pending.get_nowait()
        except queue.Empty:return
        if not self.idle():self.event(delivery,'deferred');return
        if delivery['channel']=='speech':
            if not self.agent.player.say(delivery['speech'],delivery=delivery,only_if_idle=True):self.event(delivery,'deferred')
            return
        if not self.authorize(delivery):self.event(delivery,'deferred');return
        try:
            popup=tk.Toplevel(root);popup.title('Artemis update');popup.configure(bg='#1c1426')
            tk.Label(popup,text=delivery['title'],wraplength=450,bg='#1c1426',fg='#eee2ff',font=('Segoe UI',12,'bold')).pack(padx=20,pady=20)
            tk.Label(popup,text='A saved update is available. This popup does not acknowledge or verify it.',wraplength=450,bg='#1c1426',fg='#d0bddf').pack(padx=20,pady=5)
            tk.Button(popup,text='Open local inbox',command=lambda:webbrowser.open(BASE+'/static/inbox.html'),bg='#49315f',fg='white').pack(padx=20,pady=10)
            tk.Button(popup,text='Dismiss popup',command=popup.destroy).pack(padx=20,pady=10)
            popup.update_idletasks();self.event(delivery,'completed')
        except Exception:self.event(delivery,'failed')
