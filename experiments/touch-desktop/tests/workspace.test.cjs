const {test}=require('node:test');const assert=require('node:assert/strict');
const {Model}=require('../ui/workspace.js');
test('selection, two native clicks and one double receipt stay distinct',()=>{const m=new Model();m.click('2');m.click('2');m.double('2');assert.equal(m.clicks,2);assert.equal(m.doubles,1);assert.equal(m.selected,'2');});
test('drag keeps the pickup offset and only one drop is counted',()=>{const m=new Model();m.begin({x:110,y:80},{x:30,y:20});assert.deepEqual(m.move({x:210,y:140}),{x:130,y:80});assert.equal(m.end(true),true);assert.equal(m.end(true),false);assert.equal(m.drops,1);});
test('zoom can hold an exact ratio until another deliberate event',()=>{const m=new Model();m.scale(1.5);assert.equal(m.zoom,1.5);m.click('1');assert.equal(m.zoom,1.5);m.scale(100);assert.equal(m.zoom,4);m.scale(.001);assert.equal(m.zoom,.35);});
