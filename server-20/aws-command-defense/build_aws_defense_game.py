import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def extract(path, name):
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"const {name} = (.*?);\s*$", text, re.MULTILINE)
    if not match:
        raise RuntimeError(f"Could not find {name} in {path.name}")
    return json.loads(match.group(1))


def clean_text(value):
    if isinstance(value, str):
        return re.sub(r"<[^>]+>", "", value).replace("&amp;", "&").replace("&quot;", '"')
    if isinstance(value, list):
        return [clean_text(item) for item in value]
    if isinstance(value, dict):
        return {key: clean_text(item) for key, item in value.items()}
    return value


clf = ROOT / "aws-ultimate-clf-c02.html"
aif = ROOT / "aws-ultimate-aif-c01.html"
data = clean_text({
    "clf": {"label": "Cloud Practitioner", "glossary": extract(clf, "GLOSSARY"), "questions": extract(clf, "BASE_QUESTIONS")},
    "aif": {"label": "AI Practitioner", "glossary": extract(aif, "GLOSSARY"), "questions": extract(aif, "BASE_QUESTIONS")},
})

template = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>AWS Command Defense</title>
<style>
:root{--ink:#f8fafc;--muted:#a8b4c8;--panel:#101a2b;--line:#2b3a55;--gold:#ffbd32;--cyan:#48d9ff;--green:#47e59b;--red:#ff637d}
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:#050912;color:var(--ink);font-family:Inter,Segoe UI,Arial,sans-serif;letter-spacing:0}
body{overflow-x:hidden}button{font:inherit}.hidden{display:none!important}
.stars{position:fixed;inset:0;pointer-events:none;background-image:radial-gradient(#fff8 1px,transparent 1px);background-size:33px 33px;opacity:.22}
.screen{min-height:100vh;position:relative;z-index:1}.landing{display:grid;place-items:center;padding:28px 18px;background:radial-gradient(circle at 50% 25%,#143e65 0,#081425 42%,#050912 74%)}
.launch{width:min(980px,100%);text-align:center}.brand{font-weight:950;font-size:clamp(40px,8vw,84px);line-height:.9;text-transform:uppercase}.brand b{color:var(--gold)}
.eyebrow{color:var(--cyan);font-weight:900;text-transform:uppercase;margin-bottom:16px}.lead{max-width:700px;margin:22px auto 28px;color:#c9d4e6;font-size:clamp(16px,2.5vw,21px)}
.step{margin:26px 0 12px;text-transform:uppercase;color:var(--muted);font-weight:900;font-size:13px}.choices{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
#difficulties{grid-template-columns:repeat(4,1fr)}
.choice{min-height:108px;border:1px solid var(--line);background:#0d1727cc;color:var(--ink);padding:18px;border-radius:8px;cursor:pointer;text-align:left;transition:.18s}
.choice:hover,.choice.active{border-color:var(--gold);box-shadow:0 0 0 2px #ffbd3233;background:#17243a}.choice strong{display:block;font-size:20px}.choice span{display:block;color:var(--muted);margin-top:6px;font-size:14px}
.start{margin-top:26px;background:var(--gold);color:#15110a;border:0;border-radius:7px;padding:15px 34px;font-weight:950;font-size:20px;cursor:pointer}.start:disabled{opacity:.35;cursor:not-allowed}
.offline-link{display:inline-block;margin:18px 0 0;color:var(--cyan);font-weight:850;text-decoration:none}.offline-link:hover{text-decoration:underline}
.game{height:100vh;overflow:hidden;background:linear-gradient(#071226,#081426 55%,#16131f)}canvas{position:absolute;inset:0;width:100%;height:100%}
.hud{position:absolute;z-index:3;top:0;left:0;right:0;display:flex;gap:14px;align-items:center;padding:12px 18px;background:#050912dd;border-bottom:1px solid #33415d;backdrop-filter:blur(8px)}
.hud .title{font-weight:950;margin-right:auto}.stat{font-size:13px;color:var(--muted)}.stat b{display:block;color:#fff;font-size:18px}.danger{color:var(--red)!important}
.pod{position:absolute;z-index:2;width:min(720px,calc(100% - 30px));left:50%;transform:translateX(-50%);padding:15px 18px;background:#0d1727ee;border:2px solid var(--cyan);border-radius:8px;box-shadow:0 0 26px #48d9ff35;text-align:center}
.pod .source{color:var(--cyan);text-transform:uppercase;font-size:11px;font-weight:950}.pod .prompt{font-size:clamp(15px,2.6vw,22px);font-weight:800;margin-top:5px;line-height:1.25}
.answers{position:absolute;z-index:4;left:50%;bottom:84px;transform:translateX(-50%);width:min(980px,calc(100% - 24px));display:grid;grid-template-columns:repeat(2,1fr);gap:9px}
.answer{border:1px solid #42516c;background:#101a2bea;color:#fff;border-radius:7px;min-height:57px;padding:10px 12px;text-align:left;cursor:pointer;display:flex;align-items:center;gap:11px;font-weight:750}
.answer:hover{border-color:var(--gold)}.key{display:grid;place-items:center;flex:0 0 32px;height:32px;border-radius:5px;background:#26344d;color:var(--gold);font-weight:950}.answer.good{border-color:var(--green);background:#123b32}.answer.bad{border-color:var(--red);background:#461d2a}
.basebar{position:absolute;z-index:4;bottom:0;left:0;right:0;height:66px;padding:12px 18px;background:#050912e8;border-top:1px solid #33415d;display:flex;align-items:center;gap:16px}.health{height:12px;flex:1;background:#2a1620;border-radius:8px;overflow:hidden}.health i{display:block;height:100%;background:linear-gradient(90deg,var(--red),var(--green));width:100%;transition:.3s}
.pause{border:1px solid #43526d;background:#162237;color:#fff;border-radius:6px;padding:9px 13px;cursor:pointer}.toast{position:absolute;z-index:8;top:86px;right:18px;width:min(390px,calc(100% - 36px));background:#0b1424f5;border:1px solid var(--line);border-left:5px solid var(--green);padding:14px 16px;border-radius:7px;box-shadow:0 12px 40px #0008}.toast.bad{border-left-color:var(--red)}.toast strong{display:block;margin-bottom:5px}.toast p{margin:0;color:#c8d3e4;line-height:1.35;font-size:14px}
.end{position:absolute;z-index:10;inset:0;display:grid;place-items:center;background:#040811e8;padding:20px}.result{width:min(560px,100%);background:#101a2b;border:1px solid var(--line);border-radius:8px;padding:28px;text-align:center}.result h2{font-size:40px;margin:0 0 8px}.result-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:22px 0}.result-grid div{padding:12px;background:#0a1322;border-radius:6px;color:var(--muted)}.result-grid b{display:block;color:#fff;font-size:23px}.actions{display:flex;gap:10px;justify-content:center}.actions button{padding:12px 18px;border-radius:6px;border:1px solid #3e4c66;background:#17243a;color:#fff;font-weight:900;cursor:pointer}.actions .again{background:var(--gold);color:#15110a;border-color:var(--gold)}
@media(max-width:650px){.choices,#difficulties{grid-template-columns:1fr}.choice{min-height:78px}.hud{padding:8px 10px}.hud .title{display:none}.stat b{font-size:15px}.pod{padding:11px 12px}.answers{bottom:72px;grid-template-columns:1fr;gap:6px}.answer{min-height:44px;font-size:13px;padding:6px 9px}.key{height:28px;flex-basis:28px}.basebar{height:58px}.toast{top:64px}}
</style></head><body><div class="stars"></div>
<main id="landing" class="screen landing"><section class="launch"><div class="eyebrow">AWS Learning Command</div><div class="brand">AWS <b>Command Defense</b></div><p class="lead">Defend your cloud base. Match falling definitions or destroy certification questions before they breach the perimeter.</p>
<div class="step">1. Choose your certification</div><div class="choices" id="sources"><button class="choice" data-value="clf"><strong>Cloud Practitioner</strong><span>CLF-C02 cloud services and fundamentals</span></button><button class="choice" data-value="aif"><strong>AI Practitioner</strong><span>AIF-C01 AI, ML, generative AI, and governance</span></button><button class="choice" data-value="mixed"><strong>Mixed Command</strong><span>Both certifications in one campaign</span></button></div>
<div class="step">2. Choose your defense mode</div><div class="choices" id="modes"><button class="choice" data-value="glossary"><strong>Glossary Defense</strong><span>Match definitions to AWS terms</span></button><button class="choice" data-value="exam"><strong>Exam Defense</strong><span>Answer certification questions under pressure</span></button><button class="choice" data-value="adaptive"><strong>Adaptive Campaign</strong><span>Mix definitions and full exam questions</span></button></div>
<div class="step">3. Choose your difficulty</div><div class="choices" id="difficulties"><button class="choice" data-value="beginner"><strong>Beginner</strong><span>20 seconds per target</span></button><button class="choice" data-value="intermediate"><strong>Intermediate</strong><span>15 seconds per target</span></button><button class="choice" data-value="expert"><strong>Expert</strong><span>10 seconds per target</span></button><button class="choice" data-value="insane"><strong>Insane</strong><span>5 seconds per target</span></button></div>
<button id="start" class="start" disabled>Launch Defense</button><br><a class="offline-link" href="AWS-Command-Defense-Offline.html" download>Download Offline Edition</a></section></main>
<main id="game" class="screen game hidden"><canvas id="field"></canvas><div class="hud"><div class="title">AWS COMMAND DEFENSE</div><div class="stat">Score<b id="score">0</b></div><div class="stat">Streak<b id="streak">0</b></div><div class="stat">Wave<b id="wave">1</b></div><div class="stat">Time<b id="clock">10.0</b></div><button class="pause" id="pause">Pause</button></div><div class="pod" id="pod"><div class="source" id="sourceTag"></div><div class="prompt" id="prompt"></div></div><div class="answers" id="answers"></div><div class="basebar"><b>BASE</b><div class="health"><i id="health"></i></div><span id="healthText">100%</span></div><div id="toast" class="toast hidden"></div><div id="end" class="end hidden"></div></main>
<script>const BANK=__BANK__;
let sourceChoice='',modeChoice='',difficultyChoice='',roundTime=10,pool=[],current=null,score=0,streak=0,wave=1,health=100,timeLeft=10,fall=0,running=false,paused=false,last=0,answered=false,laser=null,particles=[];
const $=id=>document.getElementById(id),shuffle=a=>{a=[...a];for(let i=a.length-1;i;i--){const j=Math.floor(Math.random()*(i+1));[a[i],a[j]]=[a[j],a[i]]}return a};
function choiceGroup(id,setter){$(id).onclick=e=>{const b=e.target.closest('.choice');if(!b)return;[...$(id).children].forEach(x=>x.classList.remove('active'));b.classList.add('active');setter(b.dataset.value);$('start').disabled=!(sourceChoice&&modeChoice&&difficultyChoice)}}
choiceGroup('sources',v=>sourceChoice=v);choiceGroup('modes',v=>modeChoice=v);choiceGroup('difficulties',v=>difficultyChoice=v);
function makeGlossary(src,g){const wrong=shuffle(src.glossary.filter(x=>x.t!==g.t)).slice(0,3).map(x=>x.t);return{type:'Glossary',prompt:g.d,correct:g.t,options:shuffle([g.t,...wrong]),explain:`${g.t}: ${g.d}`}}
function makeExam(q){let options=(q.options||[]).map((o,i)=>({text:typeof o==='string'?o:o.text,ok:i===q.correct||o.correct===true}));if(!options.some(x=>x.ok))options[0].ok=true;let correct=options.find(x=>x.ok).text;return{type:'Exam',prompt:q.q,correct,options:shuffle(options.map(x=>x.text)),explain:q.explanation||q.explain||`${correct} is the best answer for this scenario.`}}
function buildPool(){const keys=sourceChoice==='mixed'?['clf','aif']:[sourceChoice],out=[];for(const k of keys){const b=BANK[k];if(modeChoice!=='exam')for(const g of b.glossary)out.push({...makeGlossary(b,g),cert:b.label});if(modeChoice!=='glossary')for(const q of b.questions)out.push({...makeExam(q),cert:b.label})}return shuffle(out)}
function start(){roundTime={beginner:20,intermediate:15,expert:10,insane:5}[difficultyChoice];pool=buildPool();score=streak=0;wave=1;health=100;running=true;paused=false;$('landing').classList.add('hidden');$('game').classList.remove('hidden');next();requestAnimationFrame(loop)}
function next(){current=pool.pop()||buildPool()[0];answered=false;timeLeft=roundTime;fall=0;$('prompt').textContent=current.prompt;$('sourceTag').textContent=`${current.cert} · ${current.type} · ${difficultyChoice} ${roundTime}s`;$('answers').innerHTML='';current.options.forEach((o,i)=>{let b=document.createElement('button');b.className='answer';b.innerHTML=`<span class="key">${'ABCD'[i]}</span><span>${o}</span>`;b.onclick=()=>answer(i,b);$('answers').appendChild(b)});updateHud()}
function answer(i,b){if(answered||paused||b.disabled)return;answered=true;const ok=current.options[i]===current.correct;if(ok){[...$('answers').children].forEach((x,n)=>{if(current.options[n]===current.correct)x.classList.add('good')});score+=100+streak*20;streak++;laser={x:innerWidth/2,y:innerHeight-70,toY:$('pod').offsetTop+40,t:0};burst(innerWidth/2,$('pod').offsetTop+40,'#48d9ff');showToast(true,'Direct hit',current.explain);updateHud();setTimeout(next,850)}else{b.classList.add('bad');b.disabled=true;streak=0;timeLeft=Math.min(timeLeft,1.35);showToast(false,`Incorrect - ${current.correct}`,current.explain);updateHud();setTimeout(()=>{if(running&&!paused)answered=false},350)}}
function breach(){health=Math.max(0,health-20);streak=0;burst(innerWidth/2,innerHeight-80,'#ff637d');showToast(false,'Base hit',`The correct answer was ${current.correct}. ${current.explain}`);updateHud();if(health<=0)finish();else setTimeout(next,900)}
function showToast(ok,title,text){let t=$('toast');t.className='toast'+(ok?'':' bad');t.innerHTML=`<strong>${title}</strong><p>${text}</p>`;clearTimeout(t._timer);t._timer=setTimeout(()=>t.classList.add('hidden'),3000)}
function updateHud(){$('score').textContent=score;$('streak').textContent=streak;$('wave').textContent=wave;$('clock').textContent=timeLeft.toFixed(1);$('clock').className=timeLeft<3?'danger':'';$('health').style.width=health+'%';$('healthText').textContent=health+'%'}
function burst(x,y,c){for(let i=0;i<28;i++)particles.push({x,y,vx:(Math.random()-.5)*8,vy:(Math.random()-.5)*8,a:1,c})}
function draw(){let c=$('field'),ctx=c.getContext('2d'),d=devicePixelRatio||1;if(c.width!==innerWidth*d||c.height!==innerHeight*d){c.width=innerWidth*d;c.height=innerHeight*d;ctx.scale(d,d)}ctx.clearRect(0,0,innerWidth,innerHeight);ctx.strokeStyle='#1d3654';ctx.globalAlpha=.5;for(let y=90;y<innerHeight;y+=55){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(innerWidth,y);ctx.stroke()}ctx.globalAlpha=1;ctx.fillStyle='#14253d';ctx.fillRect(0,innerHeight-74,innerWidth,8);if(laser){ctx.strokeStyle='#48d9ff';ctx.lineWidth=5;ctx.shadowBlur=18;ctx.shadowColor='#48d9ff';ctx.beginPath();ctx.moveTo(laser.x,innerHeight-70);ctx.lineTo(laser.x,laser.toY);ctx.stroke();ctx.shadowBlur=0;if(++laser.t>12)laser=null}particles=particles.filter(p=>p.a>.04);for(const p of particles){p.x+=p.vx;p.y+=p.vy;p.a*=.93;ctx.globalAlpha=p.a;ctx.fillStyle=p.c;ctx.fillRect(p.x,p.y,4,4)}ctx.globalAlpha=1}
function loop(ts){if(!running)return;let dt=Math.min(.05,(ts-last)/1000||0);last=ts;if(!paused&&!answered){timeLeft-=dt;fall=1-timeLeft/roundTime;$('pod').style.top=(68+fall*(innerHeight-390))+'px';if(timeLeft<=0){answered=true;breach()}updateHud()}draw();requestAnimationFrame(loop)}
function finish(){running=false;let hits=Math.floor(score/100),e=$('end');e.classList.remove('hidden');e.innerHTML=`<section class="result"><div class="eyebrow">Defense Report</div><h2>Base Lost</h2><p>Your training data has been saved in this browser.</p><div class="result-grid"><div><b>${score}</b>Score</div><div><b>${wave}</b>Wave</div><div><b>${hits}</b>Hits</div></div><div class="actions"><button onclick="location.reload()">Change Mode</button><button class="again" onclick="restart()">Defend Again</button></div></section>`}
function restart(){$('end').classList.add('hidden');pool=buildPool();score=streak=0;wave=1;health=100;running=true;next();requestAnimationFrame(loop)}
$('start').onclick=start;$('pause').onclick=()=>{paused=!paused;$('pause').textContent=paused?'Resume':'Pause'};addEventListener('keydown',e=>{if('abcd'.includes(e.key.toLowerCase())){let i='abcd'.indexOf(e.key.toLowerCase());$('answers').children[i]?.click()}if(e.key===' ')$('pause').click()});
setInterval(()=>{if(running&&!paused){wave++;updateHud()}},30000);
</script></body></html>'''

out = ROOT / "aws-command-defense.html"
out.write_text(template.replace("__BANK__", json.dumps(data, separators=(",", ":"))), encoding="utf-8")
print(f"Wrote {out} ({out.stat().st_size:,} bytes)")
print(f"Cloud: {len(data['clf']['glossary'])} terms, {len(data['clf']['questions'])} questions")
print(f"AI: {len(data['aif']['glossary'])} terms, {len(data['aif']['questions'])} questions")
