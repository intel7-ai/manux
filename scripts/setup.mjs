import fs from 'node:fs/promises';import path from 'node:path';import{fileURLToPath}from'node:url';import{createHash}from'node:crypto';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const modelUrl='https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task';
const out=path.join(root,'public/models/gesture_recognizer.task');
await fs.mkdir(path.dirname(out),{recursive:true});
try{const info=await fs.stat(out);if(info.size>1000000){console.log('Model already present:',out);process.exit(0);}}catch{}
const response=await fetch(modelUrl,{signal:AbortSignal.timeout(60000)});if(!response.ok)throw new Error(`Model download: HTTP ${response.status}`);
const bytes=Buffer.from(await response.arrayBuffer());if(bytes.length<1000000)throw new Error('Downloaded model is unexpectedly small.');
await fs.writeFile(out+'.tmp',bytes);await fs.rename(out+'.tmp',out);
await fs.writeFile(path.join(root,'public/models/model-source.json'),JSON.stringify({url:modelUrl,sha256:createHash('sha256').update(bytes).digest('hex'),bytes:bytes.length},null,2));
console.log('Model ready:',bytes.length,'bytes');
