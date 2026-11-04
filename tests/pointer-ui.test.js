import test from 'node:test';
import assert from 'node:assert/strict';
import {guideRows, guideNote, contactText, POINTER_SETTINGS} from '../src/pointer-guide.js';
import {meanLuma, brightnessReading, LOW_LIGHT_THRESHOLD} from '../src/camera-metrics.js';

test('guide rows follow the chosen engine, click style and anchor', () => {
  assert.equal(guideRows({engine:'palm'})[0][1], '포인터 이동');
  assert.deepEqual(guideRows({engine:'index', click:'trigger', anchor:'index'}).map(r => r[1]), ['포인터 이동', '좌클릭', '우클릭', '드래그']);
  assert.deepEqual(guideRows({engine:'index', click:'off', anchor:'palm'}), [['손바닥 따라가기', '포인터 이동'], ['클릭', '꺼짐']]);
  assert.match(guideNote({engine:'index', click:'off'}), /클릭을 판정하지 않습니다/);
  assert.ok(!POINTER_SETTINGS.click.includes('pinch'), 'pinch is not offered until it is ported');
});

test('contact text names fingers like the Qt diagnostics', () => {
  assert.equal(contactText([0, 2]), '엄지 + 중지');
  assert.equal(contactText([]), '—');
});

test('brightness uses BT.601 luma and flags low light below the Qt threshold', () => {
  const pixel = (r, g, b) => [r, g, b, 255];
  assert.equal(meanLuma(new Uint8ClampedArray([...pixel(255, 255, 255), ...pixel(255, 255, 255)])), 255);
  assert.equal(Math.round(meanLuma(new Uint8ClampedArray(pixel(100, 0, 0)))), 30);
  assert.equal(meanLuma(new Uint8ClampedArray()), -1);
  assert.deepEqual(brightnessReading(LOW_LIGHT_THRESHOLD - 1), {level:LOW_LIGHT_THRESHOLD - 1, percent:27, lowLight:true});
  assert.equal(brightnessReading(200).lowLight, false);
  assert.deepEqual(brightnessReading(-1), {level:-1, percent:null, lowLight:false});
});
