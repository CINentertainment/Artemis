"""Bounded, offline extraction. Invoked in a disposable subprocess; never runs macros."""
import csv
import email
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_TEXT = 600000
MAX_UNITS = 500
MAX_EXPANDED = 100 * 1024 * 1024
SUPPORTED = {'.pdf','.docx','.xlsx','.pptx','.odt','.ods','.odp','.epub','.txt','.md','.csv','.tsv','.json','.xml','.html','.htm','.log','.rst','.yaml','.yml','.eml','.png','.jpg','.jpeg','.tif','.tiff','.bmp','.webp'}

class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.hidden=0
    def handle_starttag(self, tag, attrs):
        if tag in {'script','style'}: self.hidden+=1
        if tag in {'p','br','div','li','tr','h1','h2','h3'}: self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in {'script','style'}: self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)

def html_text(raw):
    parser=PlainHTML();parser.feed(raw);return ''.join(parser.parts)

def xml(raw):
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('XML entities and document types are unsupported.')
    return ET.fromstring(raw)

def local(tag):return tag.rsplit('}',1)[-1]

def text_nodes(node):
    return ' '.join(e.text or '' for e in node.iter() if local(e.tag) in {'t','text','p','h'} and e.text)

def tesseract():
    import os
    configured=os.environ.get('ARTEMIS_TESSERACT_PATH')
    if configured and Path(configured).is_file():return configured
    found=shutil.which('tesseract')
    if found:return found
    for base in ('C:/Program Files/Tesseract-OCR', 'C:/Program Files (x86)/Tesseract-OCR', str(Path(os.environ.get('LOCALAPPDATA',''))/'Programs/Tesseract-OCR'), str(Path(os.environ.get('LOCALAPPDATA',''))/'Tesseract-OCR')):
        p=Path(base)/'tesseract.exe'
        if p.is_file():return str(p)
    return None

def ocr_image(image, directory):
    tool=tesseract()
    if not tool:raise ValueError('OCR needs local Tesseract. Install it, then retry with OCR selected.')
    image_path=Path(directory)/'ocr.png';image.save(image_path)
    result=subprocess.run([tool,str(image_path),'stdout','-l','eng'],capture_output=True,timeout=30,check=False)
    if result.returncode:raise ValueError('Local OCR failed; check Tesseract and its English language data.')
    return result.stdout.decode('utf-8',errors='replace')

def extract(path, suffix, ocr=False):
    if suffix not in SUPPORTED:raise ValueError('Unsupported format. Convert legacy Office files to DOCX/XLSX/PPTX; archives, executables and encrypted documents are not imported.')
    units=[]; warnings=[]; consumed=0; truncated=False
    def add(label,text):
        nonlocal consumed,truncated
        text=re.sub(r'\x00','',str(text)).strip()
        if not text:return
        if len(units)>=MAX_UNITS or consumed>=MAX_TEXT:truncated=True;return
        remaining=MAX_TEXT-consumed
        if len(text)>remaining:truncated=True;text=text[:remaining]
        units.append({'locator':str(label),'text':text});consumed+=len(text)
    if suffix=='.pdf':
        from pypdf import PdfReader
        reader=PdfReader(path)
        if reader.is_encrypted:raise ValueError('Password-protected PDF. Supply a decrypted copy; passwords are not stored.')
        page_count=len(reader.pages)
        if page_count>MAX_UNITS:truncated=True
        with tempfile.TemporaryDirectory() as td:
            doc=None
            try:
                for i,page in enumerate(reader.pages[:MAX_UNITS]):
                    if consumed>=MAX_TEXT:truncated=True;break
                    value=page.extract_text() or ''
                    if len(value.strip())<30 and ocr:
                        import pypdfium2
                        if doc is None:doc=pypdfium2.PdfDocument(str(path))
                        rendered=doc[i];bitmap=rendered.render(scale=1.5)
                        try:value=ocr_image(bitmap.to_pil(),td);warnings.append(f'Page {i+1}: OCR text may contain recognition errors.')
                        finally:bitmap.close();rendered.close()
                    if len(value.strip())<30:warnings.append(f'Page {i+1}: little or no extractable text; OCR may be required.')
                    add(f'Page {i+1}',value)
            finally:
                if doc is not None:doc.close()
    elif suffix in {'.png','.jpg','.jpeg','.tif','.tiff','.bmp','.webp'}:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS=25000000
        if not ocr:raise ValueError('Image document: select OCR during upload.')
        with Image.open(path) as image, tempfile.TemporaryDirectory() as td:
            for i in range(min(getattr(image,'n_frames',1),MAX_UNITS)):
                image.seek(i);add(f'Image {i+1} (OCR)',ocr_image(image.convert('RGB'),td))
            if getattr(image,'n_frames',1)>MAX_UNITS:truncated=True
        warnings.append('OCR is English-language text recognition; image/diagram interpretation and handwriting accuracy are not guaranteed.')
    elif suffix in {'.docx','.xlsx','.pptx','.odt','.ods','.odp','.epub'}:
        with zipfile.ZipFile(path) as z:
            infos=z.infolist()
            if len(infos)>10000 or sum(x.file_size for x in infos)>MAX_EXPANDED:raise ValueError('Document archive exceeds safe extraction limits.')
            if any(x.flag_bits & 1 for x in infos):raise ValueError('Encrypted document archives are unsupported.')
            names=set(z.namelist())
            def read(name):return xml(z.read(name))
            if suffix=='.docx':
                root=read('word/document.xml');n=0
                for node in root.iter():
                    if local(node.tag)=='p':
                        n+=1;add(f'Paragraph {n}',text_nodes(node))
                for name in sorted(names):
                    if re.match(r'word/(header\d+|footer\d+|footnotes|endnotes)\.xml$',name):add(name,text_nodes(read(name)))
                warnings.append('Word references use paragraph numbers, not rendered page numbers. Text boxes, comments and embedded objects may be omitted.')
            elif suffix=='.pptx':
                slides=sorted((n for n in names if re.fullmatch(r'ppt/slides/slide\d+\.xml',n)),key=lambda n:int(re.search(r'(\d+)\.xml',n).group(1)))
                for i,name in enumerate(slides,1):add(f'Slide {i}',text_nodes(read(name)))
                warnings.append('Slide text only; speaker notes, images, charts and embedded objects are not interpreted.')
            elif suffix=='.xlsx':
                shared=[]
                if 'xl/sharedStrings.xml' in names:
                    shared=[text_nodes(n) for n in read('xl/sharedStrings.xml') if local(n.tag)=='si']
                relations={n.attrib['Id']:n.attrib['Target'] for n in read('xl/_rels/workbook.xml.rels')}
                for sheet in read('xl/workbook.xml').iter():
                    if local(sheet.tag)!='sheet':continue
                    rid=next((v for k,v in sheet.attrib.items() if local(k)=='id'),None)
                    target=relations.get(rid,'');name=target.lstrip('/') if target.startswith('/') else 'xl/'+target
                    if '..' in Path(name).parts or name not in names:raise ValueError('Unsupported spreadsheet relationship.')
                    for row in read(name).iter():
                        if local(row.tag)!='row':continue
                        values=[]
                        for cell in row:
                            if local(cell.tag)!='c':continue
                            value=next((e.text or '' for e in cell if local(e.tag)=='v'),'')
                            formula=next((e.text for e in cell if local(e.tag)=='f'),None)
                            if cell.attrib.get('t')=='s' and value:value=shared[int(value)]
                            if cell.attrib.get('t')=='inlineStr':value=text_nodes(cell)
                            if formula:value=f'Formula: {formula}; cached value: {value or "unavailable"}'
                            values.append(cell.attrib.get('r','')+'='+value)
                        add(f'Sheet {sheet.attrib.get("name", "?")} · row {row.attrib.get("r", "?")}', ' | '.join(values))
                warnings.append('Formulas are never evaluated. Cached values may be stale. Charts, formatting and embedded objects are not interpreted.')
            elif suffix=='.epub':
                # Follow OPF spine order; never fetch referenced resources.
                container=read('META-INF/container.xml')
                opf=next(n.attrib['full-path'] for n in container.iter() if local(n.tag)=='rootfile')
                package=read(opf);manifest={n.attrib.get('id'):n.attrib.get('href') for n in package.iter() if local(n.tag)=='item'}
                for i,item in enumerate((n for n in package.iter() if local(n.tag)=='itemref'),1):
                    from posixpath import normpath,join,dirname
                    target=normpath(join(dirname(opf),manifest.get(item.attrib.get('idref'),'')))
                    if target not in names: warnings.append(f'Section {i} missing or unsupported.');continue
                    add(f'Section {i}',html_text(z.read(target).decode('utf-8',errors='replace')))
            else:
                root=read('content.xml')
                for i,node in enumerate((n for n in root.iter() if local(n.tag) in {'p','h'}),1):add(f'Paragraph {i}', ''.join(node.itertext()))
                warnings.append('OpenDocument text extraction; layout and embedded media are not interpreted.')
    else:
        raw=Path(path).read_bytes()
        try:value=raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:value=raw.decode('utf-16') if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else raw.decode('cp1252');warnings.append('Non-UTF-8 text decoded using BOM or Windows-1252; review special characters.')
            except UnicodeDecodeError:raise ValueError('Unsupported text encoding; save as UTF-8.')
        if suffix in {'.html','.htm'}:value=html_text(value)
        if suffix=='.xml':value=' '.join(xml(raw).itertext())
        if suffix in {'.csv','.tsv'}:
            for i,row in enumerate(csv.reader(io.StringIO(value),delimiter='\t' if suffix=='.tsv' else ','),1):add(f'Row {i}', ' | '.join(row))
        elif suffix=='.eml':
            from email import policy
            message=email.message_from_bytes(raw,policy=policy.default)
            add('Email headers','\n'.join(f'{k}: {message.get(k, "")}' for k in ('Subject','From','To','Date')))
            for i,part in enumerate(message.walk(),1):
                if part.get_content_disposition()=='attachment':continue
                if part.get_content_type() in {'text/plain','text/html'}:
                    content=part.get_content();add(f'Email body {i}',html_text(content) if part.get_content_type()=='text/html' else content)
            warnings.append('Email attachments are not imported; upload them separately.')
        else:
            lines=value.splitlines()
            for start in range(0,len(lines),40):add(f'Lines {start+1}–{min(start+40,len(lines))}', '\n'.join(lines[start:start+40]))
    if truncated:warnings.append(f'PARTIAL extraction: maximum {MAX_UNITS} text units / {MAX_TEXT:,} characters. Split the document to read omitted content.')
    if not units:raise ValueError('No readable text found. Try OCR for scans or convert to a supported format.')
    return {'units':units,'warnings':list(dict.fromkeys(warnings)),'partial':truncated or any('little or no' in w or 'missing or unsupported' in w for w in warnings)}

if __name__=='__main__':
    try:
        result=extract(Path(sys.argv[1]),sys.argv[2],len(sys.argv)>3 and sys.argv[3]=='ocr')
        print(json.dumps(result,ensure_ascii=True))
    except Exception as e:
        print(json.dumps({'error':f'{type(e).__name__}: {str(e)[:350]}'}));sys.exit(1)
