import test from 'node:test';
import assert from 'node:assert/strict';
import {buildOverlayFrame, OVERLAY_TIP_LIMIT} from '../src/overlay-frame.js';
import {validateOverlayFrame, isOverlayDocument} from '../desktop/overlay-protocol.mjs';
import {hand} from './helpers/mouse-hand.js';

test('overlay frame describes fingertips relative to the palm and survives main-process validation', () => {
  const frame = buildOverlayFrame(hand('move'), {mode:'move', width:1280, height:720});
  assert.ok(frame);
  assert.equal(frame.mode, 'move');
  assert.deepEqual(Object.keys(frame.tips), ['thumb', 'index', 'middle', 'ring', 'pinky']);
  // Extended fingers point up from the palm center.
  assert.ok(frame.tips.index[1] < 0 && frame.tips.middle[1] < 0);
  assert.deepEqual(frame.contacts, []);
  assert.deepEqual(validateOverlayFrame(frame), frame);
});

test('overlay frame is mirrored so the user moving right reads as screen right', () => {
  const frame = buildOverlayFrame(hand('move'), {width:1, height:1});
  // In the camera image the thumb of this right hand sits left of the palm; on screen it is mirrored.
  assert.ok(frame.tips.thumb[0] > 0);
  assert.ok(frame.tips.pinky[0] < 0);
});

test('thumb contacts are reported per finger', () => {
  assert.deepEqual(buildOverlayFrame(hand('left')).contacts, ['index']);
  assert.deepEqual(buildOverlayFrame(hand('right')).contacts, ['middle']);
  assert.deepEqual(buildOverlayFrame(hand('scroll')).contacts, ['ring']);
});

test('overlay frame does not depend on hand size or position in the image', () => {
  const near = buildOverlayFrame(hand('move'));
  const far = buildOverlayFrame(hand('move', {scale:.5, x:.2, y:-.1}));
  for (const name of Object.keys(near.tips)) {
    near.tips[name].forEach((value, axis) => assert.ok(Math.abs(value - far.tips[name][axis]) < 1e-9, name));
  }
});

test('missing or degenerate hands produce no frame, and unknown modes are reported as other', () => {
  assert.equal(buildOverlayFrame(null), null);
  assert.equal(buildOverlayFrame(hand('move').slice(0, 20)), null);
  assert.equal(buildOverlayFrame(Array.from({length:21}, () => ({x:.5, y:.5}))), null);
  assert.equal(buildOverlayFrame(hand('move'), {mode:'holding-aux'}).mode, 'other');
  const tips = buildOverlayFrame(hand('move', {scale:40})).tips;
  assert.ok(Object.values(tips).flat().every(value => Math.abs(value) <= OVERLAY_TIP_LIMIT));
});

test('main process drops any overlay payload that is not exactly a frame', () => {
  const good = buildOverlayFrame(hand('right'), {mode:'right'});
  assert.ok(validateOverlayFrame(good));
  for (const bad of [
    null, [], 'frame',
    {...good, extra:1},
    {...good, mode:'click'},
    {...good, contacts:['thumb']},
    {...good, contacts:['index', 'index']},
    {...good, tips:{...good.tips, index:[NaN, 0]}},
    {...good, tips:{...good.tips, index:[5, 0]}},
    {...good, tips:{...good.tips, wrist:[0, 0]}},
  ]) assert.equal(validateOverlayFrame(bad), null);
  // The validated copy shares no references with the sender's object.
  const copy = validateOverlayFrame(good);
  copy.tips.index[0] = 3;
  assert.notEqual(good.tips.index[0], 3);
});

test('overlay window accepts only the bundled overlay page', () => {
  const origin = 'http://127.0.0.1:4175';
  assert.equal(isOverlayDocument(`${origin}/overlay.html`, origin), true);
  for (const url of [`${origin}/`, `${origin}/overlay.html?x=1`, `${origin}/overlay.html#a`, 'http://127.0.0.1:9999/overlay.html', 'https://example.com/overlay.html', 'not a url']) {
    assert.equal(isOverlayDocument(url, origin), false, url);
  }
});
