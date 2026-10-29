let recognizer;
self.onmessage=async({data})=>{
  const {id,type,bitmap,timestamp}=data;
  try{
    if(type==='init'){
      const {GestureRecognizer,FilesetResolver}=await import('/vendor/vision_bundle.mjs');
      const files=await FilesetResolver.forVisionTasks('/vendor/wasm');
      const options={baseOptions:{modelAssetPath:'/models/gesture_recognizer.task',delegate:'GPU'},runningMode:'VIDEO',numHands:1,minHandDetectionConfidence:.5,minHandPresenceConfidence:.5,minTrackingConfidence:.5};
      let delegate='GPU';
      try{recognizer=await GestureRecognizer.createFromOptions(files,options);}
      catch{delegate='CPU';options.baseOptions.delegate=delegate;recognizer=await GestureRecognizer.createFromOptions(files,options);}
      self.postMessage({id,ok:true,delegate});
    }else if(type==='frame'){
      if(!recognizer)throw new Error('인식 모델이 아직 준비되지 않았습니다.');
      const start=performance.now();
      const result=recognizer.recognizeForVideo(bitmap,timestamp);
      self.postMessage({id,ok:true,result,inferenceMs:performance.now()-start});
    }
  }catch(error){self.postMessage({id,ok:false,error:error.message});}
  finally{bitmap?.close();}
};
