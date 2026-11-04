// Gesture guide rows and notes per pointer setting, matching the Qt dashboard (hand-mouse/src/dashboard.qml).
export const POINTER_SETTINGS = Object.freeze({
  engine:Object.freeze(['palm', 'index']),
  click:Object.freeze(['trigger', 'off']),       // 'pinch' is not ported to Electron yet
  anchor:Object.freeze(['index', 'palm']),
});
export const DEFAULT_POINTER_SETTINGS = Object.freeze({engine:'palm', click:'trigger', anchor:'index'});

/** [gesture, result] rows for the "동작 안내" list. */
export function guideRows({engine, click, anchor}) {
  if (engine !== 'index') {
    return [['손을 편하게 펴고 이동', '포인터 이동'], ['엄지 + 검지', '좌클릭 · 드래그'], ['엄지 + 중지', '우클릭'], ['엄지 + 약지', '스크롤']];
  }
  if (click === 'off') return [[anchor === 'palm' ? '손바닥 따라가기' : '검지 끝 따라가기', '포인터 이동'], ['클릭', '꺼짐']];
  return [['검지만 펴기', '포인터 이동'], ['엄지로 검지 옆 짧게', '좌클릭'], ['누른 채 가만히', '우클릭'], ['누른 채 움직이기', '드래그']];
}

export function guideNote({engine, click}) {
  if (engine !== 'index') return '손끝 대신 손바닥 중심으로 이동을 읽습니다. 손가락을 접거나 주먹을 쥐면 쉬고, 손을 내리면 입력을 멈춥니다.';
  return click === 'off'
    ? '클릭을 판정하지 않습니다. 손이 보이면 손 모양과 관계없이 커서가 따라갑니다.'
    : '엄지가 검지 옆에 다가오면 커서가 고정되고, 누르기 직전 위치에서 클릭합니다. 검지를 움직이는 동안에는 클릭하지 않습니다.';
}

const FINGER_NAMES = ['엄지', '검지', '중지', '약지', '새끼'];
/** Contacts from thumbContacts() as the Qt "조합" text, e.g. "엄지 + 중지". */
export function contactText(contacts) {
  return contacts.length ? contacts.map(i => FINGER_NAMES[i] ?? String(i)).join(' + ') : '—';
}
