// Editable slide authoring. All audience-facing copy comes from deck-content.json.
import fs from 'node:fs/promises';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath, pathToFileURL} from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url));
const app=path.resolve(here,'../..');
const workspace=process.env.MANUX_WORKSPACE||path.resolve(app,'../..');
const skillDir=process.env.PRESENTATIONS_SKILL_DIR;
const runtimeModules=process.env.ARTIFACT_RUNTIME_MODULES;
const runtimePython=process.env.ARTIFACT_RUNTIME_PYTHON;
if(!skillDir||!runtimeModules||!runtimePython)throw new Error('Set PRESENTATIONS_SKILL_DIR, ARTIFACT_RUNTIME_MODULES and ARTIFACT_RUNTIME_PYTHON.');
process.env.RUNTIME_NODE_MODULES=runtimeModules;
const data=JSON.parse(await fs.readFile(path.join(here,'deck-content.json'),'utf8'));
const build=path.join(workspace,'tmp/kickoff-deck-redesign');
const out=path.join(workspace,'output/manux-kickoff-team-20261029');
await fs.mkdir(build,{recursive:true});await fs.mkdir(out,{recursive:true});
const deliveryPath=path.join(here,'MANUX_착수보고_20261029.pptx');
const revision=process.env.MANUX_DECK_REVISION||'redesign';
const candidate=path.join(build,`candidate-${revision}.pptx`);
const finalPath=path.join(out,process.env.MANUX_DECK_NAME||`MANUX_착수보고_20261029_${revision}.pptx`);
async function author(){
const {Presentation,PresentationFile,FileBlob}=await import(pathToFileURL(path.join(runtimeModules,'@oai/artifact-tool/dist/artifact_tool.mjs')).href);
try{
 const source=await PresentationFile.importPptx(await FileBlob.load(deliveryPath));
 const inspected=await source.inspect({kind:'slide,layout,notes',maxChars:10000});
 await fs.writeFile(path.join(build,'source-inspect.ndjson'),inspected.ndjson);
}catch(error){if(error.code!=='ENOENT')throw error;}
const C={ink:'#112331',paper:'#F5F7F3',green:'#087D65',mint:'#46E5BD',muted:'#54656D',line:'#CBD9D2'};
const font='Malgun Gothic';
const p=Presentation.create({slideSize:{width:1280,height:720}});
function text(s,v,x,y,w,h,size=30,color=C.ink,bold=false,name=''){
 const a=s.shapes.add({geometry:'textbox',name,position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});
 a.text=v??'';a.text.style={typeface:font,fontSize:size,color,bold,verticalAlignment:'top',alignment:'left',autoFit:'none',wrap:'square',insets:0};return a;
}
function rule(s,x,y,w,color=C.line,width=1){return s.shapes.add({geometry:'line',position:{left:x,top:y,width:w,height:0},fill:'none',line:{fill:color,width}});}
function arrow(s,a,b,fromSide='right',toSide='left',color=C.green){return s.shapes.connect(a,b,{kind:'straight',fromSide,toSide,line:{fill:color,width:1.7},tail:{type:'triangle',width:'sm',length:'sm'}});}
function base(d,i){
 const s=p.slides.add();s.background.fill=C.paper;
 if(d.layout!=='cover'){text(s,d.title,64,44,1152,82,d.titleSize||48,C.ink,true,'title');text(s,String(i+1).padStart(2,'0'),1166,675,48,25,18,C.muted);}
 if(d.eyebrow)text(s,d.eyebrow,72,133,1136,32,23,C.green,true,'scope-label');
 s.speakerNotes.text=(d.notes||'')+(d.sources?.length?'\n\n근거\n'+d.sources.join('\n'):'');return s;
}
function bottom(s,v){if(v)text(s,v,68,612,1126,60,24,C.muted,false,'footnote');}
function balancedTwoLines(value){
 const words=value.split(' ');if(words.length<3||value.includes('\n'))return value;
 const width=s=>Array.from(s).reduce((n,c)=>n+(/[\u3131-\uD79D]/.test(c)?1:c===' '?0.3:0.55),0);
 let best=1,cost=Infinity;
 for(let j=1;j<words.length;j++){const gap=Math.abs(width(words.slice(0,j).join(' '))-width(words.slice(j).join(' ')));if(gap<cost){cost=gap;best=j;}}
 return words.slice(0,best).join(' ')+'\n'+words.slice(best).join(' ');
}
async function image(s,asset,alt,x,y,w,h){if(!asset)return;s.images.add({blob:new Uint8Array(await fs.readFile(path.resolve(here,asset))),contentType:'image/png',fit:'contain',alt:alt||'',position:{left:x,top:y,width:w,height:h}});}
function flatTable(s,d){
 const values=d.headers?[d.headers,...d.rows]:d.rows,top=d.tableTop||164,height=d.tableHeight||415;
 const presets={alternatives:[325,365,462],roles:[180,390,582],schedule:[185,500,467]};
 const widths=d.columnWidths||presets[d.layout]||(values[0].length===2?[290,862]:[390,360,402]);
 const t=s.tables.add({rows:values.length,columns:values[0].length,left:64,top,width:1152,height,columnWidths:widths,values});
 t.styleOptions={headerRow:false,bandedRows:false};t.borders.assign({fill:'none',width:0});
 let rowBottom=top;
 for(let r=0;r<values.length;r++){
  const isHeader=!!d.headers&&r===0;const rowHeight=d.headers?(isHeader?45:(height-45)/d.rows.length):height/values.length;
  t.rows[r].height=rowHeight;rowBottom+=rowHeight;
  for(let c=0;c<values[r].length;c++){
   const cell=t.getCell(r,c);cell.fill='none';cell.text.style={typeface:font,fontSize:isHeader?22:(d.fontSize||28),color:isHeader?C.green:(c===0?C.ink:C.muted),bold:isHeader||c===0,alignment:'left',verticalAlignment:'middle',insets:{top:6,right:10,bottom:6,left:8},autoFit:'none',wrap:'square'};
  }
  if(r<values.length-1)rule(s,72,rowBottom,1136,isHeader?C.green:C.line,isHeader?1.5:1);
 }
 return t;
}
function columns(s,d){
 const n=d.columns.length,gap=60,w=(1136-gap*(n-1))/n;
 d.columns.forEach((a,j)=>{const x=72+j*(w+gap);text(s,a.title,x,170,w,64,d.columnTitleSize||37,C.green,true);text(s,a.main,x,266,w,145,d.mainSize||44,C.ink,true);if(a.detail)text(s,a.detail,x,455,w,108,d.detailSize||29,C.muted);});
}
for(let i=0;i<data.slides.length;i++){
 const d=data.slides[i],s=base(d,i);
 switch(d.layout){
  case 'cover':
   s.background.fill=C.ink;
   await image(s,d.imageAsset||'assets/manux-logo.png',d.imageAlt||data.title,65,70,540,162);
   text(s,d.coverTitle||d.title,72,300,1130,180,d.coverTitleSize||62,C.paper,true,'cover-title');
   text(s,d.subtitle||'',76,529,1080,54,32,C.mint,true);
   text(s,d.bottom||d.date||data.date||'',76,606,1080,40,24,C.paper);break;
  case 'statement':
   text(s,d.lead,72,173,1120,190,d.leadSize||55,C.ink,true);
   (d.points||[]).forEach((v,j)=>text(s,v,72,395+j*64,1120,54,d.fontSize||34,C.green));
   bottom(s,d.bottom);break;
  case 'columns':case 'validation':columns(s,d);bottom(s,d.bottom);break;
  case 'alternatives':case 'roles':case 'schedule':case 'table':
   if(d.lead)text(s,d.lead,72,126,1136,47,28,C.green);
   flatTable(s,d);bottom(s,d.bottom);break;
  case 'market':
   text(s,d.metric,64,193,590,150,d.metricSize||116,C.green,true);
   text(s,d.metricLabel,72,381,590,115,32,C.ink);
   text(s,d.rightTitle,675,196,535,65,39,C.ink,true);
   (d.rightPoints||[]).forEach((v,j)=>text(s,v,675,294+j*89,535,77,32,C.muted));
   bottom(s,d.bottom);break;
  case 'demo':
   if(d.lead)d.lead.split('\n').forEach((v,j)=>text(s,v,72,198+j*86,383,64,d.leadSize||36,C.green,true));
   await image(s,d.imageAsset,d.imageAlt,470,174,746,390);bottom(s,d.bottom);break;
  case 'problem':
   text(s,d.lead,68,162,464,154,d.leadSize||49,C.ink,true);
   (d.points||[]).forEach((v,j)=>text(s,j===0?v.replace(' × ','\n× '):balancedTwoLines(v),72,345+j*103,464,96,j===0?32:40,j===0?C.muted:C.green,j>0));
   await image(s,d.imageAsset,d.imageAlt,556,166,660,372);bottom(s,d.bottom);break;
  case 'rock':case 'pairs':
   if(d.lead)text(s,d.lead,68,139,1130,64,40,C.green,true);
   if(d.headers){text(s,d.headers[0],72,174,280,38,23,C.green,true);text(s,d.headers[1],391,174,805,38,23,C.green,true);rule(s,72,216,1136,C.green,1.5);}
   d.rows.forEach(([a,b],j)=>{const y=238+j*82;text(s,a,72,y,280,62,33,C.ink,true);text(s,b,391,y,805,62,33,C.muted);if(j<d.rows.length-1)rule(s,72,y+67,1136);});bottom(s,d.bottom);break;
  case 'pipeline':{
   if(d.lead)text(s,d.lead,72,132,1136,47,28,C.muted);
   const count=d.stages.length,gap=33,w=(1136-gap*(count-1))/count;
   const anchors=d.stages.map((a,j)=>{
    const x=72+j*(w+gap);const node=text(s,a.title,x,207,w,66,d.stageTitleSize||32,C.ink,true,'pipeline-stage');
    rule(s,x,292,w,C.green,1.5);text(s,a.detail,x,315,w,111,d.stageDetailSize||25,C.muted);
    if(a.data)text(s,a.data,x,434,w,76,d.stageDataSize||21,C.green);return node;
   });
   for(let j=0;j<count-1;j++)arrow(s,anchors[j],anchors[j+1]);
   if(d.example){text(s,d.example.label,72,531,165,35,23,C.green,true);text(s,d.example.text,255,531,950,55,27,C.ink);}
   bottom(s,d.bottom);break;
  }
  case 'sequence':{
   if(d.lead)text(s,d.lead,72,130,1136,48,31,C.green,true);
   const rowHeight=d.rowHeight||73,top=182;
   d.steps.forEach((a,j)=>{
    const y=top+j*rowHeight;text(s,String(j+1).padStart(2,'0'),72,y+1,61,45,25,C.green,true);
    text(s,a.title,157,y,287,54,d.stepTitleSize||31,C.ink,true);text(s,a.detail,466,y,400,60,d.stepDetailSize||25,C.muted);
    if(a.data)text(s,a.data,906,y,304,60,d.stepDataSize||23,C.green);
    if(j<d.steps.length-1)rule(s,157,y+rowHeight-7,1054);
   });
   if(d.example){text(s,d.example.label,72,559,165,35,23,C.green,true);text(s,d.example.text,255,559,950,47,26,C.ink);}bottom(s,d.bottom);break;
  }
  case 'lanes':{
   const rowHeight=d.rowHeight||144,top=184;const laneNodes=[];
   d.lanes.forEach((lane,j)=>{
    const y=top+j*rowHeight;text(s,lane.title,72,y,225,73,30,C.ink,true);if(lane.status)text(s,lane.status,72,y+78,225,36,22,C.muted);
    const count=lane.steps.length,gap=42,w=(872-gap*(count-1))/count;
    const nodes=lane.steps.map((value,k)=>text(s,typeof value==='string'?value:value.title,337+k*(w+gap),y+7,w,62,d.laneTextSize||28,C.ink,k===count-1));
    for(let k=0;k<nodes.length-1;k++)arrow(s,nodes[k],nodes[k+1]);
    if(lane.note)text(s,lane.note,337,y+78,872,29,22,C.muted);laneNodes.push(nodes);
   });
   for(let j=0;j<laneNodes.length-1;j++)s.shapes.connect(laneNodes[j].at(-1),laneNodes[j+1][0],{kind:'elbow',fromSide:'bottom',toSide:'top',line:{fill:C.green,width:1.3},tail:{type:'triangle',width:'sm',length:'sm'}});
   bottom(s,d.bottom);break;
  }
  default:throw new Error(`Unsupported layout ${d.layout} on slide ${i+1}`);
 }
}
await(await PresentationFile.exportPptx(p)).save(candidate);
}
async function renderBatch(start,end){
 const {PresentationFile,FileBlob}=await import(pathToFileURL(path.join(runtimeModules,'@oai/artifact-tool/dist/artifact_tool.mjs')).href);
const deck=await PresentationFile.importPptx(await FileBlob.load(candidate));
for(let i=Number(start);i<Number(end);i++){
 const png=await deck.export({slide:deck.slides.items[i],format:'png',scale:1});
 await fs.writeFile(path.join(build,'slide-'+String(i+1).padStart(2,'0')+'.png'),new Uint8Array(await png.arrayBuffer()));
}
}
async function finalize(){
const {finalizePresentation}=await import(pathToFileURL(path.join(skillDir,'container_tools/artifact_tool_utils.mjs')).href);
const tableOwners=data.slides.map((d,i)=>['alternatives','roles','schedule','table'].includes(d.layout)?i+1:null).filter(Boolean);
const result=await finalizePresentation({workspaceDir:workspace,candidatePath:candidate,finalPath,pythonExecutable:runtimePython,integrityValidatorPath:path.join(skillDir,'container_tools/inspect_presentation_package_integrity.py'),layoutValidatorPath:path.join(skillDir,'container_tools/inspect_presentation_layout_geometry.py'),layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-bullet-geometry','--validate-heading-fit',...tableOwners.flatMap(x=>['--require-native-table-slide',String(x)])],explicitTotalSlideCount:data.slides.length,requiredNativeTableOwnerSlides:tableOwners,fontPolicy:{basis:'design',families:['Malgun Gothic']},verifyArtifactToolImport:true,receiptPath:path.join(build,path.basename(finalPath)+'.validation.json')});
await fs.copyFile(finalPath,deliveryPath);
console.log(JSON.stringify({finalPath,deliveryPath,result}));
}
// Keep native authoring, rendering, and re-import validation in separate lifetimes.
const phase=process.env.MANUX_BUILD_PHASE;
if(phase==='author')await author();
else if(phase==='render')await renderBatch(process.argv[2],process.argv[3]);
else if(phase==='finalize')await finalize();
else{
 const run=(phase,args=[])=>execFileSync(process.execPath,[fileURLToPath(import.meta.url),...args],{env:{...process.env,MANUX_BUILD_PHASE:phase},stdio:'inherit'});
 run('author');
 for(let start=0;start<data.slides.length;start+=4)run('render',[String(start),String(Math.min(start+4,data.slides.length))]);
 run('finalize');
}
