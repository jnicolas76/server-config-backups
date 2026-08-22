import json
import re
from pathlib import Path

import pdfplumber


ROOT = Path(__file__).resolve().parent
PDF = ROOT / "civics-bilingual.pdf"
OUT = ROOT / "index.html"


def clean(value):
    value = re.sub(r"128 Civics Questions and Answers \(2025 version\)", "", value)
    value = re.sub(r"128 preguntas y respuestas de civismo \(versión 2025\)", "", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_bank():
    lines = []
    with pdfplumber.open(PDF) as document:
        for page in document.pages:
            text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
            lines.extend(text.splitlines())

    starts = []
    for index, line in enumerate(lines):
        match = re.match(r"^(\d{1,3})\.\s+(.+)$", line.strip())
        if match and 1 <= int(match.group(1)) <= 128:
            starts.append((index, int(match.group(1))))

    blocks = []
    for pos, (start, number) in enumerate(starts):
        end = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
        raw = [line.strip() for line in lines[start:end] if line.strip()]
        question = re.sub(r"^\d{1,3}\.\s+", "", raw[0])
        cursor = 1
        while cursor < len(raw) and not raw[cursor].startswith("●"):
            if not re.match(r"^(128 Civics|128 preguntas|\d+)$", raw[cursor]):
                question += " " + raw[cursor]
            cursor += 1
        answers = []
        current = ""
        for line in raw[cursor:]:
            if re.match(r"^(128 Civics|128 preguntas|American Government|GOBIERNO|AMERICAN HISTORY|HISTORIA|INTEGRATED CIVICS|EDUCACIÓN|\d+)$", line):
                continue
            if line.startswith("●"):
                if current:
                    answers.append(clean(current))
                current = line[1:].strip()
            elif current:
                current += " " + line
        if current:
            answers.append(clean(current))
        blocks.append({"number": number, "question": clean(question), "answers": answers})

    pairs = {}
    for block in blocks:
        pairs.setdefault(block["number"], []).append(block)
    if set(pairs) != set(range(1, 129)):
        missing = sorted(set(range(1, 129)) - set(pairs))
        raise RuntimeError(f"Question extraction incomplete; missing {missing}")

    categories = [
        (1, 15, "Principles of American Government", "Principios del gobierno estadounidense"),
        (16, 62, "System of Government", "Sistema de gobierno"),
        (63, 72, "Rights and Responsibilities", "Derechos y responsabilidades"),
        (73, 88, "Colonial Period and Independence", "Periodo colonial e independencia"),
        (89, 99, "The 1800s", "Los años 1800"),
        (100, 118, "Recent American History", "Historia estadounidense reciente"),
        (119, 128, "Symbols and Holidays", "Símbolos y días festivos"),
    ]
    bank = []
    for number in range(1, 129):
        entries = pairs[number]
        if len(entries) < 2:
            raise RuntimeError(f"Question {number} does not have both languages")
        category = next(item for item in categories if item[0] <= number <= item[1])
        bank.append({
            "id": number,
            "special": "*" in entries[0]["question"] or "*" in entries[1]["question"],
            "category": {"en": category[2], "es": category[3]},
            "en": entries[0],
            "es": entries[1],
        })

    # Location-specific answers configured for Colorado ZIP code 80015.
    colorado_answers = {
        23: {
            "en": ["Michael Bennet", "John Hickenlooper"],
            "es": ["Michael Bennet", "John Hickenlooper"],
        },
        29: {
            "en": ["Jason Crow (Colorado's 6th Congressional District)"],
            "es": ["Jason Crow (Distrito congresional 6 de Colorado)"],
        },
        61: {"en": ["Jared Polis"], "es": ["Jared Polis"]},
        62: {"en": ["Denver"], "es": ["Denver"]},
    }
    for number, localized_answers in colorado_answers.items():
        bank[number - 1]["en"]["answers"] = localized_answers["en"]
        bank[number - 1]["es"]["answers"] = localized_answers["es"]
    return bank


TEMPLATE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Civics Command Center - 2025 Test</title>
<style>
:root{--ink:#f7f5ef;--muted:#adb8c8;--panel:#101a2a;--line:#28364b;--blue:#3ec6e0;--red:#e84f5f;--gold:#ffbd3d;--green:#49df9a;--bg:#060b13}*{box-sizing:border-box}body{margin:0;color:var(--ink);font:16px/1.45 system-ui,Segoe UI,sans-serif;background:radial-gradient(circle at 80% -10%,#183652 0,transparent 35%),var(--bg)}button,input,select{font:inherit}.shell{max-width:1180px;margin:auto;padding:20px}.top{display:flex;align-items:center;gap:14px;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:15px}.brand{font-weight:900;font-size:25px;letter-spacing:0}.brand b{color:var(--gold)}.spacer{flex:1}.pill,.btn{border:1px solid var(--line);color:var(--ink);background:#172235;border-radius:6px;padding:10px 15px;font-weight:750;cursor:pointer}.btn.primary{background:var(--gold);color:#15100a;border-color:var(--gold)}.btn.good{background:var(--green);color:#06150e;border-color:var(--green)}.btn.danger{background:var(--red);border-color:var(--red)}.lang{display:flex}.lang button{border-radius:0}.lang button:first-child{border-radius:6px 0 0 6px}.lang button:last-child{border-radius:0 6px 6px 0}.lang .on{background:var(--blue);color:#061018}.hero{padding:52px 0 24px;display:grid;grid-template-columns:1.3fr .7fr;gap:35px}.hero h1{font-size:clamp(38px,7vw,76px);line-height:.95;margin:0 0 18px}.hero p{font-size:19px;color:var(--muted);max-width:700px}.seal{min-height:250px;display:grid;place-items:center;position:relative}.seal:before{content:'★';font-size:110px;color:var(--gold);border:10px double var(--blue);width:210px;height:210px;border-radius:50%;display:grid;place-items:center;box-shadow:0 0 55px #3ec6e044}.modegrid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:25px 0}.mode{padding:20px;background:var(--panel);border:1px solid var(--line);border-radius:8px;cursor:pointer;min-height:145px}.mode:hover{border-color:var(--gold)}.mode .icon{font-size:32px}.mode h3{margin:10px 0 5px}.mode p{margin:0;color:var(--muted);font-size:14px}.view{display:none}.view.active{display:block}.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:22px;margin:16px 0}.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.progress{height:9px;background:#202c3d;border-radius:8px;overflow:hidden}.progress span{height:100%;display:block;background:var(--gold);width:0}.question{font-size:clamp(23px,4vw,38px);font-weight:850;margin:20px 0}.answers{display:grid;gap:10px}.answer{text-align:left;padding:15px;border:1px solid var(--line);background:#142033;color:var(--ink);border-radius:6px;cursor:pointer}.answer:hover{border-color:var(--blue)}.answer.correct{border-color:var(--green);background:#103627}.answer.wrong{border-color:var(--red);background:#401925}.feedback{display:none;margin-top:15px;padding:16px;border-left:5px solid var(--gold);background:#0b1422}.feedback.show{display:block}.studygrid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.card{border:1px solid var(--line);background:#111d2d;padding:17px;border-radius:7px}.card .num{color:var(--gold);font-weight:900}.card .a{color:var(--muted);margin-top:8px}.flash{min-height:360px;display:grid;place-items:center;text-align:center;cursor:pointer}.flash .front{font-size:30px;font-weight:850}.flash .back{display:none;color:var(--green);font-size:23px}.flash.flipped .front{display:none}.flash.flipped .back{display:block}.gamebox{height:520px;position:relative;overflow:hidden;background:linear-gradient(#071a30,#0e1826 72%,#2b2616 72%);border:1px solid var(--line);border-radius:8px}.falling{position:absolute;top:20px;left:7%;right:7%;padding:18px;background:#132239;border:2px solid var(--blue);border-radius:8px;text-align:center}.base{position:absolute;bottom:14px;left:10%;right:10%;height:75px;border:3px solid var(--gold);background:#242219;clip-path:polygon(0 100%,10% 30%,30% 30%,35% 0,65% 0,70% 30%,90% 30%,100% 100%)}.laser{display:none;position:absolute;bottom:82px;left:50%;width:4px;height:330px;background:var(--green);box-shadow:0 0 18px var(--green);transform-origin:bottom}.gameanswers{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-top:12px}.gameanswers button{min-height:62px}.alert{border-left:4px solid var(--red);padding:12px;background:#2c1720;color:#ffd9de}.footer{color:var(--muted);font-size:13px;border-top:1px solid var(--line);margin-top:35px;padding:18px 0}.search{width:min(420px,100%);padding:12px;border-radius:6px;border:1px solid var(--line);background:#0b1422;color:white}@media(max-width:780px){.hero{grid-template-columns:1fr}.seal{display:none}.modegrid{grid-template-columns:repeat(2,1fr)}.studygrid{grid-template-columns:1fr}.gameanswers{grid-template-columns:1fr}.shell{padding:14px}.hero{padding-top:30px}}
</style></head><body><main class="shell">
<header class="top"><div class="brand">CIVICS <b>COMMAND CENTER</b></div><div class="spacer"></div><button class="btn" id="homeBtn">⌂ <span data-t="home">Home</span></button><div class="lang"><button class="btn on" data-lang="en">English</button><button class="btn" data-lang="es">Español</button></div></header>
<section id="home" class="view active"><div class="hero"><div><div class="row"><div class="pill">2025 USCIS • 128</div><div class="pill" data-t="configured">Colorado • ZIP 80015</div></div><h1 data-t="hero">Prepare. Practice. Pass.</h1><p data-t="intro">A bilingual study center built from the 2025 civics question bank. Train with realistic exams, instant explanations, flashcards, and an arcade defense game.</p></div><div class="seal"></div></div>
<div class="alert" data-t="notice">Official answers involving elected or appointed officials can change. Verify them before your interview.</div>
<div class="modegrid"><div class="mode" data-open="exam"><div class="icon">✓</div><h3 data-t="exam">Exam Simulator</h3><p data-t="examdesc">20 randomized questions. Pass with 12 correct.</p></div><div class="mode" data-open="practice"><div class="icon">◎</div><h3 data-t="practice">Practice</h3><p data-t="practicedesc">Immediate feedback and useful context.</p></div><div class="mode" data-open="study"><div class="icon">▤</div><h3 data-t="study">Study & Flashcards</h3><p data-t="studydesc">Browse all 128 or drill one card at a time.</p></div><div class="mode" data-open="game"><div class="icon">⌁</div><h3 data-t="game">Civics Defense</h3><p data-t="gamedesc">Protect the base before time runs out.</p></div></div></section>
<section id="exam" class="view"><div class="panel"><div class="row"><h2 data-t="exam">Exam Simulator</h2><div class="spacer"></div><span id="examStat"></span></div><div class="progress"><span id="examProgress"></span></div><div id="examQuestion" class="question"></div><div id="examAnswers" class="answers"></div><div id="examFeedback" class="feedback"></div><div class="row" style="margin-top:15px"><button id="examNext" class="btn primary" data-t="next">Next</button><button id="examRestart" class="btn" data-t="restart">New Exam</button></div></div></section>
<section id="practice" class="view"><div class="panel"><div class="row"><h2 data-t="practice">Practice</h2><select id="practiceCategory" class="pill"></select><button id="practiceNew" class="btn" data-t="newq">New Question</button></div><div id="practiceQuestion" class="question"></div><div id="practiceAnswers" class="answers"></div><div id="practiceFeedback" class="feedback"></div></div></section>
<section id="study" class="view"><div class="panel"><div class="row"><h2 data-t="study">Study & Flashcards</h2><input id="studySearch" class="search" data-ph="search" placeholder="Search questions or answers"><button id="flashMode" class="btn" data-t="flashcards">Flashcards</button></div></div><div id="studyList" class="studygrid"></div><div id="flashCard" class="panel flash" style="display:none"><div class="front"></div><div class="back"></div></div><div id="flashControls" class="row" style="display:none"><button id="flashPrev" class="btn">←</button><button id="flashShuffle" class="btn" data-t="shuffle">Shuffle</button><button id="flashNext" class="btn">→</button></div></section>
<section id="game" class="view"><div class="panel"><div class="row"><h2 data-t="game">Civics Defense</h2><select id="difficulty" class="pill"><option value="20">Beginner • 20s</option><option value="15">Intermediate • 15s</option><option value="10">Expert • 10s</option><option value="5">Insane • 5s</option></select><button id="gameStart" class="btn primary" data-t="start">Start Mission</button><span id="gameScore"></span><span id="defense"></span></div></div><div class="gamebox"><div id="falling" class="falling" style="display:none"></div><div id="laser" class="laser"></div><div class="base"></div></div><div id="gameAnswers" class="gameanswers"></div></section>
<footer class="footer"><span data-t="source">Source: 2025 USCIS Civics Test. This is an independent study tool, not an official USCIS product.</span> <a style="color:var(--blue)" href="https://www.uscis.gov/citizenship/testupdates" target="_blank" data-t="updates">Check official answer updates</a>.</footer>
</main><script>const BANK=__BANK__;
const T={en:{home:'Home',configured:'Colorado • ZIP 80015',hero:'Prepare. Practice. Pass.',intro:'A bilingual study center built from the 2025 civics question bank. Train with realistic exams, instant explanations, flashcards, and an arcade defense game.',notice:'Colorado-specific answers are filled in for ZIP 80015. Officials can change, so verify them before your interview.',exam:'Exam Simulator',examdesc:'20 randomized questions. Pass with 12 correct.',practice:'Practice',practicedesc:'Immediate feedback and useful context.',study:'Study & Flashcards',studydesc:'Browse all 128 or drill one card at a time.',game:'Civics Defense',gamedesc:'Protect the base before time runs out.',next:'Next',restart:'New Exam',newq:'New Question',flashcards:'Flashcards',shuffle:'Shuffle',start:'Start Mission',search:'Search questions or answers',source:'Source: 2025 USCIS Civics Test. This is an independent study tool, not an official USCIS product.',updates:'Check official answer updates',correct:'Correct',incorrect:'Incorrect',accepted:'Accepted answers',all:'All categories',score:'Score',defense:'Defense',over:'Mission over',passed:'Passed',failed:'Not passed'},es:{home:'Inicio',configured:'Colorado • Código postal 80015',hero:'Prepárese. Practique. Apruebe.',intro:'Un centro de estudio bilingüe basado en las 128 preguntas del examen cívico de 2025. Practique con exámenes, explicaciones, tarjetas y un juego de defensa.',notice:'Las respuestas específicas de Colorado están configuradas para el código postal 80015. Los funcionarios pueden cambiar; verifíquelos antes de su entrevista.',exam:'Simulador de examen',examdesc:'20 preguntas al azar. Apruebe con 12 respuestas correctas.',practice:'Práctica',practicedesc:'Retroalimentación inmediata y contexto útil.',study:'Guía y tarjetas',studydesc:'Estudie las 128 preguntas o use tarjetas.',game:'Defensa Cívica',gamedesc:'Proteja la base antes de que se acabe el tiempo.',next:'Siguiente',restart:'Nuevo examen',newq:'Nueva pregunta',flashcards:'Tarjetas',shuffle:'Mezclar',start:'Iniciar misión',search:'Buscar preguntas o respuestas',source:'Fuente: Examen de Civismo de USCIS 2025. Esta es una herramienta independiente, no un producto oficial de USCIS.',updates:'Ver actualizaciones oficiales',correct:'Correcto',incorrect:'Incorrecto',accepted:'Respuestas aceptadas',all:'Todas las categorías',score:'Puntos',defense:'Defensa',over:'Misión terminada',passed:'Aprobado',failed:'No aprobado'}};
let lang='en',exam=[],examIndex=0,examCorrect=0,answered=false,practiceQ=null,flashIndex=0,gameTimer=null,gameStartAt=0,gameSeconds=20,gameDefense=100,gamePoints=0,gameQ=null;
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)],shuffle=a=>[...a].sort(()=>Math.random()-.5),tr=k=>T[lang][k]||k;
function qtext(q){return q[lang].question.replace(/\s*\*\s*$/,'')} function answers(q){return q[lang].answers.filter(Boolean)}
function choices(q){let pool=BANK.filter(x=>x.category.en===q.category.en&&x.id!==q.id).flatMap(x=>answers(x).slice(0,1));return shuffle([answers(q)[0],...shuffle(pool).slice(0,3)])}
function setLang(l){lang=l;$$('[data-lang]').forEach(b=>b.classList.toggle('on',b.dataset.lang===l));$$('[data-t]').forEach(e=>e.textContent=tr(e.dataset.t));$$('[data-ph]').forEach(e=>e.placeholder=tr(e.dataset.ph));renderStudy();if(practiceQ)renderPractice(practiceQ);if(exam.length)renderExam();}
function openView(id){$$('.view').forEach(v=>v.classList.remove('active'));$('#'+id).classList.add('active');scrollTo(0,0);if(id==='exam'&&!exam.length)newExam();if(id==='practice')newPractice();if(id==='study')renderStudy();}
$$('[data-open]').forEach(x=>x.onclick=()=>openView(x.dataset.open));$('#homeBtn').onclick=()=>openView('home');$$('[data-lang]').forEach(b=>b.onclick=()=>setLang(b.dataset.lang));
function newExam(){exam=shuffle(BANK).slice(0,20);examIndex=0;examCorrect=0;renderExam()}
function renderExam(){answered=false;let q=exam[examIndex];if(!q){let pass=examCorrect>=12;$('#examQuestion').textContent=`${pass?tr('passed'):tr('failed')}: ${examCorrect}/20`;$('#examAnswers').innerHTML='';$('#examFeedback').className='feedback';$('#examStat').textContent='';$('#examProgress').style.width='100%';return}$('#examStat').textContent=`${examIndex+1}/20 • ${tr('score')}: ${examCorrect}`;$('#examProgress').style.width=`${examIndex/20*100}%`;$('#examQuestion').textContent=`${q.id}. ${qtext(q)}`;renderAnswerButtons($('#examAnswers'),q,(ok)=>{if(ok)examCorrect++;showFeedback($('#examFeedback'),q,ok)});$('#examFeedback').className='feedback'}
function renderAnswerButtons(root,q,cb){root.innerHTML='';let correct=answers(q)[0];choices(q).forEach(a=>{let b=document.createElement('button');b.className='answer';b.textContent=a;b.onclick=()=>{if(answered)return;answered=true;let ok=a===correct;b.classList.add(ok?'correct':'wrong');[...root.children].forEach(x=>{if(x.textContent===correct)x.classList.add('correct')});cb(ok)};root.appendChild(b)})}
function showFeedback(root,q,ok){root.className='feedback show';root.innerHTML=`<b>${ok?tr('correct'):tr('incorrect')}</b><br>${tr('accepted')}: ${answers(q).join(' • ')}<br><small>${q.category[lang]}${q.special?' • 65/20':''}</small>`}
$('#examNext').onclick=()=>{if(answered||!exam[examIndex]){examIndex++;renderExam()}};$('#examRestart').onclick=newExam;
let cats=[...new Set(BANK.map(q=>q.category.en))];$('#practiceCategory').innerHTML=`<option value="">All categories</option>`+cats.map(c=>`<option>${c}</option>`).join('');
function newPractice(){let c=$('#practiceCategory').value,pool=c?BANK.filter(q=>q.category.en===c):BANK;practiceQ=pool[Math.floor(Math.random()*pool.length)];renderPractice(practiceQ)}
function renderPractice(q){answered=false;$('#practiceQuestion').textContent=`${q.id}. ${qtext(q)}`;renderAnswerButtons($('#practiceAnswers'),q,ok=>showFeedback($('#practiceFeedback'),q,ok));$('#practiceFeedback').className='feedback'}$('#practiceNew').onclick=newPractice;$('#practiceCategory').onchange=newPractice;
function renderStudy(){let term=($('#studySearch').value||'').toLowerCase(),items=BANK.filter(q=>JSON.stringify(q).toLowerCase().includes(term));$('#studyList').innerHTML=items.map(q=>`<article class="card"><div class="num">${q.id} • ${q.category[lang]}${q.special?' • 65/20':''}</div><b>${qtext(q)}</b><div class="a">${answers(q).join(' • ')}</div></article>`).join('');renderFlash()}
$('#studySearch').oninput=renderStudy;function renderFlash(){let q=BANK[flashIndex];$('#flashCard').classList.remove('flipped');$('#flashCard .front').textContent=`${q.id}. ${qtext(q)}`;$('#flashCard .back').textContent=answers(q).join(' • ')}
$('#flashMode').onclick=()=>{let show=$('#flashCard').style.display==='none';$('#studyList').style.display=show?'none':'grid';$('#flashCard').style.display=show?'grid':'none';$('#flashControls').style.display=show?'flex':'none'};$('#flashCard').onclick=()=>$('#flashCard').classList.toggle('flipped');$('#flashPrev').onclick=()=>{flashIndex=(flashIndex+127)%128;renderFlash()};$('#flashNext').onclick=()=>{flashIndex=(flashIndex+1)%128;renderFlash()};$('#flashShuffle').onclick=()=>{flashIndex=Math.floor(Math.random()*128);renderFlash()};
function startGame(){clearInterval(gameTimer);gameSeconds=+$('#difficulty').value;gameDefense=100;gamePoints=0;nextGameQ()}
function nextGameQ(){gameQ=BANK[Math.floor(Math.random()*128)];gameStartAt=Date.now();$('#falling').style.display='block';$('#falling').style.top='20px';$('#falling').textContent=qtext(gameQ);answered=false;let root=$('#gameAnswers');root.innerHTML='';choices(gameQ).forEach((a,i)=>{let b=document.createElement('button');b.className='answer';b.textContent=String.fromCharCode(65+i)+'. '+a;b.onclick=()=>gameAnswer(a===answers(gameQ)[0]);root.appendChild(b)});clearInterval(gameTimer);gameTimer=setInterval(gameTick,50);updateGameStats()}
function gameTick(){let p=(Date.now()-gameStartAt)/(gameSeconds*1000);$('#falling').style.top=`${20+Math.min(1,p)*330}px`;if(p>=1){gameDefense-=25;nextOrEnd()}}
function gameAnswer(ok){if(answered)return;answered=true;if(ok){gamePoints+=100;$('#laser').style.display='block';setTimeout(()=>$('#laser').style.display='none',180)}else{gameDefense-=20;gameStartAt-=gameSeconds*500}setTimeout(nextOrEnd,ok?230:500)}
function nextOrEnd(){clearInterval(gameTimer);if(gameDefense<=0){$('#falling').textContent=tr('over')+` • ${gamePoints}`;$('#gameAnswers').innerHTML='';gameDefense=0;updateGameStats()}else nextGameQ()}
function updateGameStats(){$('#gameScore').textContent=`${tr('score')}: ${gamePoints}`;$('#defense').textContent=`${tr('defense')}: ${gameDefense}%`}$('#gameStart').onclick=startGame;
setLang('en');</script></body></html>'''


def main():
    bank = parse_bank()
    OUT.write_text(TEMPLATE.replace("__BANK__", json.dumps(bank, ensure_ascii=False)), encoding="utf-8")
    print(f"Wrote {OUT} with {len(bank)} bilingual questions")


if __name__ == "__main__":
    main()
