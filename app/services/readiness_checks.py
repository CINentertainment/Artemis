"""Explicit local OCR diagnostics and a known-input OCR smoke test."""
import json,subprocess,tempfile,time
from pathlib import Path
from threading import RLock
_CACHE={};LOCK=RLock()
def ocr_diagnostics(force=False):
    with LOCK:
        if not force and time.time()-_CACHE.get('checked_at',0)<60:return dict(_CACHE)
        from app.services.document_extract import tesseract
        tool=tesseract();data={'checked_at':time.time(),'executable_found':bool(tool),'english_available':False,'runnable':False}
        if tool:
            try:
                result=subprocess.run([tool,'--list-langs'],capture_output=True,timeout=10,check=False)
                data['runnable']=result.returncode==0
                data['english_available']='eng' in result.stdout.decode('utf-8',errors='replace').splitlines()
            except (OSError,subprocess.TimeoutExpired):pass
        _CACHE.clear();_CACHE.update(data);return dict(data)

def ocr_smoke():
    data=ocr_diagnostics(True)
    if not data['runnable'] or not data['english_available']:return data|{'passed':False,'message':'Install a runnable Tesseract with English language data.'}
    from PIL import Image,ImageDraw,ImageFont
    from app.services.document_extract import ocr_image
    image=Image.new('RGB',(1000,200),'white');draw=ImageDraw.Draw(image)
    font=None
    for name in ['C:/Windows/Fonts/arial.ttf','DejaVuSans.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
        try:font=ImageFont.truetype(name,54);break
        except OSError:pass
    if font is None:font=ImageFont.load_default(size=54)
    draw.text((35,60),'ARTEMIS 7429',font=font,fill='black')
    try:
        with tempfile.TemporaryDirectory(prefix='artemis-ocr-check-') as folder:text=ocr_image(image,folder)
        passed='ARTEMIS' in text.upper() and '7429' in text
    except Exception:passed=False
    if passed:
        from app.services.capability_awareness import record_success
        record_success('ocr','known_input_image_recognized')
    return data|{'passed':passed,'message':'Known-input OCR image recognized.' if passed else 'OCR did not reproduce the known-input fixture; inspect the installation.'}
if __name__=='__main__':
    value=ocr_smoke();print(json.dumps(value));raise SystemExit(0 if value['passed'] else 1)
