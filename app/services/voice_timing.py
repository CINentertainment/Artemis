"""Pure timing/calibration helpers shared by the local listener tests."""
import re

def calibrate(levels):
    if len(levels)<15:raise ValueError('Not enough microphone samples. Unmute and retry calibration.')
    values=sorted(max(0,float(x))for x in levels);floor=values[int((len(values)-1)*.8)]
    if floor>.04:raise ValueError('The room was too loud for calibration. Stay quiet and retry.')
    return floor,min(.09,max(.0035,floor*2+.001))

def end_ready(elapsed,silence,has_speech,command_seen,end_silence=.55):
    if elapsed>=12:return True
    return has_speech and silence>=end_silence and(command_seen or elapsed>=2.5)

def speech_chunks(text,first=140,later=450):
    # Prefer complete sentences while bounding the first audio request.
    words=re.findall(r'\S+',text);parts=[];current='';limit=first
    for word in words:
        if len(word)>limit:
            if current:parts.append(current);current='';limit=later
            while len(word)>limit:parts.append(word[:limit]);word=word[limit:];limit=later
        if current and len(current)+len(word)+1>limit:parts.append(current);current='';limit=later
        current+=(' 'if current else'')+word
        if len(parts)==0 and re.search(r'[.!?]["\u201d]?$',current)and len(current)>=35:parts.append(current);current='';limit=later
    if current:parts.append(current)
    return parts
