#!/usr/bin/env python3
import argparse
import html
import json
import os
import re
import subprocess
import time
import zipfile
from pathlib import Path

import numpy as np
from kokoro_onnx import Kokoro

MODEL = Path('/home/jnicolas/cinemediavault-lab/tts-models/kokoro-v1.0.onnx')
VOICES = Path('/home/jnicolas/cinemediavault-lab/tts-models/voices-v1.0.bin')
VOICE_MAP = {'male': 'am_michael', 'female': 'af_heart'}


def atomic_json(path, payload):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    os.replace(temp, path)


def clean_html(raw):
    raw = re.sub(r'<(script|style|nav)\b[^>]*>.*?</\1>', ' ', raw, flags=re.I | re.S)
    text = html.unescape(re.sub(r'<[^>]+>', ' ', raw))
    return re.sub(r'\s+', ' ', text).strip()


def epub_sections(path):
    values=[]
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(('.xhtml','.html','.htm')):
                continue
            lowered=name.lower()
            if any(token in lowered for token in ('cover','titlepage','title_page','copyright','toc.','contents','frontmatter','front-matter')):
                continue
            text=clean_html(archive.read(name).decode('utf-8','ignore'))
            values.append((name,text))
    return values


def pdf_sections(path):
    run=subprocess.run(['pdftotext','-layout',str(path),'-'],capture_output=True,text=True,timeout=600,check=True)
    pages=[re.sub(r'\s+',' ',part).strip() for part in run.stdout.split('\f')]
    return [(f'Page {i+1}',text) for i,text in enumerate(pages) if len(text)>100]


def chunks(text, limit=420):
    out=[]; buffer=''
    for sentence in re.split(r'(?<=[.!?])\s+',text):
        words=sentence.split()
        while len(' '.join(words))>limit:
            take=[]
            while words and len(' '.join(take+[words[0]]))<=limit: take.append(words.pop(0))
            if buffer: out.append(buffer); buffer=''
            if take: out.append(' '.join(take))
        candidate=(buffer+' '+(' '.join(words))).strip()
        if buffer and len(candidate)>limit: out.append(buffer); buffer=' '.join(words)
        else: buffer=candidate
    if buffer: out.append(buffer)
    return [x for x in out if x]


def main():
    p=argparse.ArgumentParser(); p.add_argument('--book',required=True); p.add_argument('--voice',choices=VOICE_MAP,required=True); p.add_argument('--output',required=True); p.add_argument('--status',required=True); p.add_argument('--title',default='Book')
    a=p.parse_args(); book=Path(a.book).resolve(); output=Path(a.output); status=Path(a.status); output.parent.mkdir(parents=True,exist_ok=True)
    state={'state':'running','title':a.title,'voice':a.voice,'scope':'full','progress':0,'message':'Full audiobook generation in progress: extracting book text','created_at':int(time.time()),'expires_at':0,'pid':os.getpid(),'output_path':str(output)}; atomic_json(status,state)
    try:
        sections=epub_sections(book) if book.suffix.lower()=='.epub' else pdf_sections(book)
        if not sections: raise RuntimeError('No readable text was found in this book')
        sections=[item for item in sections if len(item[1])>=250 and not any(x in item[1].lower()[:240] for x in ('table of contents','copyright','cover image','book cover'))]
        work=[]
        for label,text in sections:
            work.extend((label,x) for x in chunks(text))
        if not work: raise RuntimeError('No narratable text was found')
        temp=output.with_suffix('.part.mp3'); kokoro=Kokoro(str(MODEL),str(VOICES)); voice=VOICE_MAP[a.voice]
        ffmpeg=subprocess.Popen(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','f32le','-ar','24000','-ac','1','-i','pipe:0','-codec:a','libmp3lame','-b:a','64k',str(temp)],stdin=subprocess.PIPE)
        for i,(label,text) in enumerate(work,1):
            samples,rate=kokoro.create(text,voice=voice,speed=1.0,lang='en-us')
            samples=np.asarray(samples,dtype=np.float32)
            if rate != 24000: raise RuntimeError(f'Unexpected Kokoro sample rate {rate}')
            ffmpeg.stdin.write(samples.tobytes()); ffmpeg.stdin.write(np.zeros(int(rate*.16),dtype=np.float32).tobytes())
            if i==1 or i%3==0 or i==len(work):
                state.update(progress=round(i/len(work)*100,1),message=f'Narrating {label} · segment {i} of {len(work)}'); atomic_json(status,state)
        ffmpeg.stdin.close(); code=ffmpeg.wait()
        if code: raise RuntimeError(f'FFmpeg exited with status {code}')
        os.replace(temp,output)
        state.update(state='ready',progress=100,message='Full audiobook ready; retained for 7 days',size=output.stat().st_size,completed_at=int(time.time()),expires_at=int(time.time())+7*24*3600,pid=0); atomic_json(status,state)
    except BaseException as exc:
        for candidate in (output,output.with_suffix('.part.mp3')):
            try: candidate.unlink(missing_ok=True)
            except Exception: pass
        state.update(state='failed',message=str(exc),pid=0); atomic_json(status,state)
        raise


if __name__=='__main__': main()
