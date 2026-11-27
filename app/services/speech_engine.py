"""Isolated persistent Kokoro worker: newline JSON in, framed WAV response out."""
import base64,contextlib,io,json,sys

def serve():
    model=None;signature=None
    for raw in sys.stdin.buffer:
        request={}
        try:
            request=json.loads(raw);key=(request['model_path'],request['voices_path'])
            with contextlib.redirect_stdout(sys.stderr):
                import onnxruntime as ort
                import soundfile as sf
                from kokoro_onnx import Kokoro
                if model is None or signature!=key:
                    options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
                    session=ort.InferenceSession(key[0],sess_options=options,providers=['CPUExecutionProvider'])
                    model=Kokoro.from_session(session,key[1]);signature=key
                samples,rate=model.create(request['text'],voice='bf_emma',speed=min(1.4,max(.75,float(request.get('speed',1)))),lang='en-gb')
                if not len(samples):raise ValueError('Speech engine returned empty audio.')
                stream=io.BytesIO();sf.write(stream,samples,rate,format='WAV',subtype='PCM_16')
            result={'id':request['id'],'audio':base64.b64encode(stream.getvalue()).decode('ascii')}
        except Exception as error:result={'id':request.get('id'),'error':type(error).__name__+': '+str(error)[:1000]}
        sys.stdout.write(json.dumps(result)+'\n');sys.stdout.flush()
if __name__=='__main__':serve()
