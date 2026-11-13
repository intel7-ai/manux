"""Live training targets; gesture IDs stay separate from shared hand-shape labels."""
from manux.compat.hand_mouse import custom_postures, llm_binding, path_gesture, posture_teach, sequence
from manux.compat.hand_mouse.action_bindings import POSTURE_NAMES_KO
from manux.compat.hand_mouse.pose_probe import POSTURE_TO_GESTURE


def fingerprint(registry):
    paths = (registry.path, custom_postures.PATH, llm_binding.HISTORY_PATH,
             sequence.DEFAULT_PATH, path_gesture.DEFAULT_PATH, posture_teach.EXAMPLES_DIR,
             posture_teach.MODEL_PATH)
    result = []
    for path in paths:
        try:
            stat = path.stat()
            result.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            result.append((str(path), None, None))
    return tuple(result)


def rows(registry):
    custom_postures.register()
    named = custom_postures.load()
    counts = posture_teach.example_counts()
    definitions = {g['key']: g for g in llm_binding.gesture_list(registry)}
    current = [h['gesture_key'] for h in llm_binding.History().rows(registry) if h['current']]
    keys = list(dict.fromkeys(current + list(registry.composite_gestures) + list(registry.custom_motions)))
    static = {gesture: posture for posture, gesture in POSTURE_TO_GESTURE.items()}
    static['thumb_middle_drag'] = 'thumb_middle'
    out, represented = [], set()

    def pose(key):
        trainable = key in POSTURE_NAMES_KO and key not in llm_binding.OPEN_FAMILY and key != 'any'
        reason = '' if trainable else '이 손 방향·조건은 현재 손 모양 MLP 학습 대상이 아닙니다.'
        return dict(key=key, name=POSTURE_NAMES_KO.get(key, key), count=counts.get(key, 0),
                    trainable=trainable, reason=reason)

    for key in keys:
        g = definitions.get(key)
        if not g:
            continue
        postures = g.get('sequence') or [g.get('posture') or static.get(key, '')]
        postures = list(dict.fromkeys(str(p) for p in postures if p))
        represented.update(postures)
        poses = [pose(p) for p in postures]
        out.append(dict(id='gesture:' + key, name=g['name'], poses=poses,
                        count=sum(p['count'] for p in poses), enabled=g['enabled'],
                        description=g['instruction'],
                        reason='' if poses else '그리기·포인터 동작에는 추가 학습할 고정 손 모양이 없습니다.'))
    for key, name in named.items():
        if key not in represented:
            out.append(dict(id='posture:' + key, name=name, poses=[pose(key)], count=counts.get(key, 0),
                            enabled=True, description='등록한 손 모양 · 명령 연결 전에도 학습할 수 있습니다.', reason=''))
    return out
