"""One-time local Google Desktop OAuth with PKCE and a loopback callback.

Run on the Artemis Windows PC. No passwords, token URLs or credentials printed.
"""
import argparse
import base64
import hashlib
import json
import secrets
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlsplit, parse_qs
import httpx
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.services.encryption import protect_text
from app.services.outreach_core import SCOPES,Gmail

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--client',type=Path,required=True);args=parser.parse_args()
    try:app=json.loads(args.client.read_text(encoding='utf-8-sig'))['installed']
    except Exception:raise SystemExit('Choose the Desktop app OAuth client JSON downloaded from your Google Cloud project.')
    client_id=app.get('client_id','');client_secret=app.get('client_secret','')
    if not client_id.endswith('.apps.googleusercontent.com'):raise SystemExit('Invalid Google Desktop application client ID.')
    state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(64);challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    result={};callback='/callback/'+secrets.token_urlsafe(12)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            u=urlsplit(self.path);q=parse_qs(u.query)
            if u.path!=callback or q.get('state')!=[state]:
                self.send_response(400);self.end_headers();self.wfile.write(b'Invalid authorization callback.');return
            if 'error' in q:result['error']=True
            elif len(q.get('code',[]))==1:result['code']=q['code'][0]
            else:result['error']=True
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.end_headers()
            self.wfile.write(b'<h1>Authorization returned</h1><p>Return to the Artemis setup window for the verification result. You may close this tab.</p>')
    server=HTTPServer(('127.0.0.1',0),Handler);server.timeout=1
    redirect='http://127.0.0.1:'+str(server.server_port)+callback
    query={'client_id':client_id,'redirect_uri':redirect,'response_type':'code','scope':' '.join(SCOPES),
           'access_type':'offline','prompt':'consent','state':state,'code_challenge':challenge,'code_challenge_method':'S256','login_hint':'business@cinentertainment.net'}
    print('Opening Google authorization. Sign in as business@cinentertainment.net, the owner of the Artemis send-as alias.')
    print('This grants Gmail read access for response detection, sending, and alias verification. Artemis will store only matching outreach conversations.')
    webbrowser.open('https://accounts.google.com/o/oauth2/v2/auth?'+urlencode(query))
    deadline=time.time()+300
    try:
        while not result and time.time()<deadline:server.handle_request()
    finally:server.server_close()
    if not result.get('code'):raise SystemExit('Authorization was not completed. Nothing connected.')
    try:
        with httpx.Client(timeout=25,trust_env=False,follow_redirects=False) as c:
            r=c.post('https://oauth2.googleapis.com/token',data={'client_id':client_id,'client_secret':client_secret,
                'code':result['code'],'code_verifier':verifier,'redirect_uri':redirect,'grant_type':'authorization_code'})
            r.raise_for_status();data=r.json()
        if not set(SCOPES).issubset(set(data.get('scope','').split())) or not data.get('refresh_token'):raise ValueError()
        credential={'client_id':client_id,'client_secret':client_secret,'refresh_token':data['refresh_token'],'scopes':list(SCOPES)}
        with Gmail(credential) as client:client.authorize();binding=client.binding()
        path=ROOT/'.runtime'/'outreach-mailbox.dpapi';path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp');temp.write_text(protect_text(json.dumps(credential)),encoding='utf-8');temp.replace(path)
    except Exception:raise SystemExit('Mailbox verification failed. Check Gmail API enablement, all requested scopes, and the accepted Artemis send-as alias. No connection saved.') from None
    print('PASS: verified '+binding['mailbox']+' with sender '+binding['sender']+'. Credentials saved with Windows user encryption.')
    print('Open http://127.0.0.1:8200/static/outreach.html, click Connect saved mailbox, enter your business postal address and enable your policy.')
if __name__=='__main__':main()
