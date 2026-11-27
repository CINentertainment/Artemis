"""One-time, explicit download from the Vosk publisher. No microphone activation."""
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlparse
from zipfile import ZipFile
import tempfile,shutil,json
ROOT=Path(__file__).resolve().parents[1];STATE=ROOT/'.runtime/voice-agent';NAME='vosk-model-small-en-us-0.15'
def main():
    target=STATE/'models'/NAME
    if target.is_dir():print('Wake-phrase model already installed:',target);return
    url='https://alphacephei.com/vosk/models/'+NAME+'.zip'
    print('Downloading local recognition model from:',url)
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent)as tmp:
        archive=Path(tmp)/'model.zip'
        with urlopen(url,timeout=90)as response,archive.open('wb')as output:
            if urlparse(response.url).hostname!='alphacephei.com':raise RuntimeError('Unexpected model download host.')
            total=0
            while True:
                block=response.read(1024*1024)
                if not block:break
                total+=len(block)
                if total>100*1024*1024:raise RuntimeError('Model archive exceeded its expected size bound.')
                output.write(block)
        with ZipFile(archive)as z:
            if sum(i.file_size for i in z.infolist())>250*1024*1024:raise RuntimeError('Unpacked model exceeds size bound.')
            for item in z.infolist():
                p=Path(item.filename)
                if p.is_absolute()or'..'in p.parts or not p.parts or p.parts[0]!=NAME:raise RuntimeError('Unexpected model archive path.')
            z.extractall(tmp)
        source=Path(tmp)/NAME
        if not(source/'am/final.mdl').is_file():raise RuntimeError('Speech model is incomplete.')
        source.replace(target)
    print('Installed local wake-phrase model. Audio is not sent to the model publisher.')
    print('Model attribution/license: https://alphacephei.com/vosk/models (small English model, Apache 2.0).')
    from vosk import Model
    model=Model(str(target))
    if hasattr(model,'vosk_model_find_word')and model.vosk_model_find_word('artemis')<0:
        print('NOTE: This model does not contain Artemis in its vocabulary. Choose hey assistant on the Voice Control page, or use the existing Record voice button until a custom Artemis model is trained.')
if __name__=='__main__':main()
