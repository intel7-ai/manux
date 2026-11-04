(function(root, factory){
  const api = factory();
  if(typeof module==='object' && module.exports) module.exports=api;
  else root.WorkspaceModel=api;
})(typeof globalThis==='object'?globalThis:this, function(){
  class Model {
    constructor(){this.reset();}
    reset(){this.clicks=0;this.doubles=0;this.drops=0;this.zoom=1;this.selected=null;this.drag=null;}
    click(id){this.clicks++;this.selected=id;return `버튼 ${id} 선택됨`;}
    double(id){this.doubles++;return `버튼 ${id} 더블클릭됨`;}
    begin(point, origin){this.drag={start:{...point},origin:{...origin}};}
    move(point){return this.drag?{x:this.drag.origin.x+point.x-this.drag.start.x,y:this.drag.origin.y+point.y-this.drag.start.y}:null;}
    end(inside){if(!this.drag)return false;this.drag=null;if(inside)this.drops++;return inside;}
    scale(factor){this.zoom=Math.min(4,Math.max(.35,this.zoom*factor));return this.zoom;}
  }
  return {Model};
});
