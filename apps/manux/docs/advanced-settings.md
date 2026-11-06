# 고급 설정과 v0.3 대응표

2026-11-07 인계 §4.6. 비교 원본: `hand-mouse/config/gestures.json`, `hand-mouse/src/settings_panel.py`.
표는 **현 런타임의 편집 연결**이며 이전 설정을 자동 변환한다는 뜻이 아니다. 구형 파일과 보존된 `v03.*` 키는 고치지 않는다.

## 사용과 적용

메인 설정의 **고급 설정**에서 그룹·이름으로 찾고 숫자·슬라이더·체크박스로 바꾼 뒤 적용한다.
열기·닫기는 값을 바꾸지 않는다. 바꾼 키만 검증해 프레임 경계에서 제어·누른 버튼·관성을 해제하고 저장한다.
프로필이 지정한 값은 읽기 전용이다. 잘못된 타입·범위·시작/해제 거리·최소/최대 관계는 전체 변경을 거부한다.
간단 민감도 화면은 핀치 시작 거리 변경 시 해제 거리를 +0.02로 맞춘다. 고급 설정은 독립 편집하므로 두 값을 함께 확인한다.
카메라·프로필·모델·판정 정책은 전용 화면에서 변경한다. 영상 비율과 재앵커 간격은 런타임 내부 값으로 제외한다.

## 기존 설정 대응

`cursor.*`는 Windows 출력, `pinch.*`·`pointer.*`·`motion.*`·`control.*`는 해당 기능에서 소비한다.
동작 enabled는 설정, action은 바인딩이다. 중첩 동작 설정 전체를 숫자 설정으로 취급하지 않는다.

| v0.3 키 | 기존 설정 창 | v0.5 연결 / 미지원 사유 |
| --- | --- | --- |
| `active_margin` | 있음 | `cursor.active_margin` |
| `approach_freeze_distance` | 파일 | `pinch.approach_freeze_distance` |
| `click_confirm_time` | 있음 | `pinch.click_confirm_time` / 검지 방식 `pointer.click_confirm_time` 별도 |
| `click_dominance_ratio` | 파일 | 미지원: 같은 의미의 런타임 소비 키 없음 |
| `click_hold_joint_ratio` | 파일 | `pinch.click_hold_joint_ratio` |
| `click_lockout_after_grab` | 파일 | `pinch.click_lockout_after_grab` |
| `click_neighbor_ratio` | 파일 | `pinch.click_neighbor_ratio` |
| `click_release_grace` | 있음 | `pinch.click_release_grace` / 검지 방식 `pointer.click_release_grace` 별도 |
| `click_release_margin` | 있음 | `pinch.click_release_margin` / 검지 방식 `pointer.click_release_margin` 별도 |
| `click_threshold` | 있음 | `pinch.click_threshold` / 검지 방식 `pointer.click_threshold` 별도 |
| `click_tuck_ratio` | 파일 | `pinch.click_tuck_ratio` |
| `cursor_anchor_ratio` | 파일 | `pinch.cursor_anchor_ratio` |
| `cursor_interpolation` | 있음 | 미지원: 고주파 커서 보간 미이식 |
| `desktop_after_activity` | 파일 | `pinch.desktop_after_activity` |
| `desktop_extended_ratio` | 있음 | `pinch.desktop_extended_ratio` |
| `desktop_hold` | 있음 | `pinch.desktop_hold` |
| `desktop_min_palm_width` | 파일 | `pinch.desktop_min_palm_width` |
| `desktop_min_tip_gap` | 파일 | `pinch.desktop_min_tip_gap` |
| `desktop_still_move` | 파일 | `pinch.desktop_still_move` |
| `desktop_thumb_tuck` | 있음 | `pinch.desktop_thumb_tuck` |
| `double_click_move_tolerance` | 있음 | `cursor.double_click_move_tolerance` |
| `double_click_window` | 있음 | `cursor.double_click_window` |
| `drag_clear_grace` | 파일 | `pinch.drag_clear_grace` |
| `drag_clear_ratio` | 파일 | `pinch.drag_clear_ratio` |
| `drag_hold_entry_window` | 파일 | `pinch.drag_hold_entry_window` |
| `drag_hold_threshold` | 파일 | `pinch.drag_hold_threshold` |
| `fist_confirm_time` | 있음 | `pinch.fist_confirm_time` |
| `fist_curl_enter` | 있음 | `pinch.fist_curl_enter` |
| `fist_curl_exit` | 파일 | `pinch.fist_curl_exit` |
| `fist_dpi_multiplier` | 있음 | `pinch.fist_dpi_multiplier` |
| `fling_gain_x` | 파일 | 미지원: Record & Fling 경로 재생 미이식 |
| `fling_gain_y` | 파일 | 미지원: Record & Fling 경로 재생 미이식 |
| `fling_strength` | 있음 | 미지원: Record & Fling 경로 재생 미이식 |
| `gesture_exclusive_time` | 있음 | `pinch.gesture_exclusive_time` |
| `grab_scroll_enabled` | 있음 | `pinch.grab_scroll_enabled` |
| `hand_lost_reset` | 파일 | `app.hand_lost_reset` |
| `index_tuck_ratio` | 파일 | `pinch.index_tuck_ratio` |
| `long_press_drag` | 있음 | `pinch.long_press_drag` |
| `long_press_drag_multiplier` | 있음 | `pinch.long_press_drag_multiplier` |
| `long_press_time` | 있음 | `pinch.long_press_time` |
| `motion_enabled` | 파일 | `motion.enabled` |
| `movement_mode` | 파일 | `cursor.movement_mode` |
| `pinch_fistlike_confirm` | 있음 | `pinch.pinch_fistlike_confirm` / 검지 방식 `pointer.pinch_fistlike_confirm` 별도 |
| `pinch_hold_joint_ratio` | 파일 | `pinch.pinch_hold_joint_ratio` |
| `pinch_rearm_time` | 파일 | `pinch.pinch_rearm_time` |
| `pinch_release_grace` | 있음 | `pinch.pinch_release_grace` / 검지 방식 `pointer.pinch_release_grace` 별도 |
| `pinch_threshold` | 있음 | `pinch.pinch_threshold` / 검지 방식 `pointer.pinch_threshold` 별도 |
| `pinky_click_threshold` | 있음 | `pinch.pinky_click_threshold` |
| `pinky_ring_dominance` | 파일 | `pinch.pinky_ring_dominance` |
| `pinky_ring_min_gap` | 파일 | `pinch.pinky_ring_min_gap` |
| `pinky_tuck_ratio` | 파일 | `pinch.pinky_tuck_ratio` |
| `record_fling_enabled` | 있음 | 미지원: Record & Fling 경로 재생 미이식 |
| `record_gain_max` | 있음 | 미지원: Record & Fling 경로 재생 미이식 |
| `record_gain_min` | 있음 | 미지원: Record & Fling 경로 재생 미이식 |
| `record_min_length` | 파일 | 미지원: Record & Fling 경로 재생 미이식 |
| `record_release_trim` | 파일 | 미지원: Record & Fling 경로 재생 미이식 |
| `record_smoothing` | 파일 | 미지원: Record & Fling 경로 재생 미이식 |
| `record_speed_high` | 있음 | 미지원: Record & Fling 경로 재생 미이식 |
| `record_speed_low` | 파일 | 미지원: Record & Fling 경로 재생 미이식 |
| `relative_accel_speed_high` | 파일 | `cursor.relative_accel_speed_high` |
| `relative_accel_speed_low` | 파일 | `cursor.relative_accel_speed_low` |
| `relative_gain_max` | 있음 | `cursor.relative_gain_max` |
| `relative_gain_min` | 있음 | `cursor.relative_gain_min` |
| `release_threshold` | 파일 | `pinch.release_threshold` / 검지 방식 `pointer.release_threshold` 별도 |
| `replay_max_duration` | 있음 | 미지원: Record & Fling 경로 재생 미이식 |
| `replay_min_duration` | 파일 | 미지원: Record & Fling 경로 재생 미이식 |
| `ring_click_threshold` | 있음 | `pinch.ring_click_threshold` / 검지 방식 `pointer.ring_click_threshold` 별도 |
| `ring_tuck_ratio` | 파일 | `pinch.ring_tuck_ratio` |
| `scroll_center_cursor` | 파일 | 미지원: UI Automation 스크롤 대상 선택·정렬 미이식 |
| `scroll_center_fallback_window` | 파일 | 미지원: UI Automation 스크롤 대상 선택·정렬 미이식 |
| `scroll_center_max_fraction` | 파일 | 미지원: UI Automation 스크롤 대상 선택·정렬 미이식 |
| `scroll_center_tolerance` | 파일 | 미지원: UI Automation 스크롤 대상 선택·정렬 미이식 |
| `scroll_cursor_hold` | 파일 | `pinch.scroll_cursor_hold` |
| `scroll_deadzone` | 파일 | `cursor.scroll_deadzone` |
| `scroll_freeze_delay` | 파일 | `pinch.scroll_freeze_delay` |
| `scroll_freeze_ratio` | 파일 | `pinch.scroll_freeze_ratio` |
| `scroll_gain` | 있음 | `cursor.scroll_gain` |
| `scroll_inertia` | 있음 | `cursor.scroll_inertia` |
| `scroll_inertia_axis_lock` | 파일 | `cursor.scroll_inertia_axis_lock` |
| `scroll_inertia_max_speed` | 파일 | `cursor.scroll_inertia_max_speed` |
| `scroll_inertia_min_speed` | 파일 | `cursor.scroll_inertia_min_speed` |
| `scroll_inertia_seamless` | 파일 | 미지원: 핀치 개방 도중 관성 연결 미이식 |
| `scroll_inertia_time` | 있음 | `cursor.scroll_inertia_time` |
| `scroll_invert` | 있음 | `cursor.scroll_invert` |
| `scroll_release_grace` | 파일 | `pinch.scroll_release_grace` |
| `scroll_release_margin` | 파일 | `pinch.scroll_release_margin` |
| `scroll_series_dwell` | 파일 | 미지원: UI Automation 스크롤 대상 선택·정렬 미이식 |
| `scroll_series_gap` | 파일 | 미지원: UI Automation 스크롤 대상 선택·정렬 미이식 |
| `scroll_session_hold` | 파일 | 미지원: UI Automation 스크롤 대상 선택·정렬 미이식 |
| `scroll_smooth_tau` | 파일 | `cursor.scroll_smooth_tau` |
| `scroll_target_uia` | 파일 | 미지원: UI Automation 스크롤 대상 선택·정렬 미이식 |
| `scroll_target_wait` | 파일 | 미지원: UI Automation 스크롤 대상 선택·정렬 미이식 |
| `shape_curl_fist` | 파일 | `pinch.shape_curl_fist` |
| `shape_curl_open` | 파일 | `pinch.shape_curl_open` |
| `shape_curl_smooth` | 파일 | `pinch.shape_curl_smooth` |
| `shape_dpi_curve` | 파일 | `pinch.shape_dpi_curve` |
| `shape_dpi_enabled` | 파일 | `pinch.shape_dpi_enabled` |
| `shape_dpi_min` | 파일 | `pinch.shape_dpi_min` |
| `shape_freeze_distance` | 파일 | `pinch.shape_freeze_distance` |
| `side_pinch_exclusive` | 파일 | `pinch.side_pinch_exclusive` |
| `smoothing` | 있음 | `cursor.smoothing` |
| `start_in_tray` | 있음 | 미지원: 시작 트레이 숨김 미연결 |
| `switcher_confirm_delay` | 있음 | 미지원: 누른 채 창 선택 미이식(현재 단발 Alt+Tab) |
| `switcher_fist_multiplier` | 있음 | 미지원: 누른 채 창 선택 미이식(현재 단발 Alt+Tab) |
| `switcher_lost_confirm` | 파일 | 미지원: 누른 채 창 선택 미이식(현재 단발 Alt+Tab) |
| `switcher_navigation` | 파일 | 미지원: 누른 채 창 선택 미이식(현재 단발 Alt+Tab) |
| `switcher_step` | 있음 | 미지원: 누른 채 창 선택 미이식(현재 단발 Alt+Tab) |
| `switcher_timeout` | 있음 | 미지원: 누른 채 창 선택 미이식(현재 단발 Alt+Tab) |
| `tremor_beta` | 있음 | 미지원: One Euro 손떨림 필터 미이식(EMA 별도) |
| `tremor_deadzone` | 있음 | 미지원: One Euro 손떨림 필터 미이식(EMA 별도) |
| `tremor_filter` | 파일 | 미지원: One Euro 손떨림 필터 미이식(EMA 별도) |
| `tremor_min_cutoff` | 있음 | 미지원: One Euro 손떨림 필터 미이식(EMA 별도) |
| `ui_magnet_enabled` | 있음 | 미지원: 대상 근처 감속 미이식 |
| `ui_magnet_max_size` | 파일 | 미지원: 대상 근처 감속 미이식 |
| `ui_magnet_min_factor` | 있음 | 미지원: 대상 근처 감속 미이식 |
| `ui_magnet_radius` | 있음 | 미지원: 대상 근처 감속 미이식 |

### 중첩 동작 설정

| v0.3 경로 | 현재 연결 |
| --- | --- |
| `gestures.fist.enabled` | `pinch.enable_fist` |
| `gestures.no_pinch.enabled` | `pinch.enable_no_pinch` |
| `gestures.thumb_index_pinch.enabled` | `pinch.grab_scroll_enabled`(스크롤만); 구형 grab 경로 재생 의미 미지원 |
| `gestures.thumb_middle_pinch.enabled` | `pinch.enable_thumb_middle_pinch` |
| `gestures.thumb_middle_pinch.cooldown` | 미지원: 핀치는 한 번 집을 때 한 번 실행; 손을 떼어 다시 시작하며 시간 쿨다운을 사용하지 않음 |
| `gestures.thumb_ring_pinch.enabled` | `pinch.enable_thumb_ring_pinch` |
| `gestures.thumb_ring_pinch.cooldown` | 미지원: 핀치는 한 번 집을 때 한 번 실행; 손을 떼어 다시 시작하며 시간 쿨다운을 사용하지 않음 |
| `gestures.thumb_index_middle_pinch.enabled` | `pinch.enable_thumb_index_middle_pinch` |
| `gestures.thumb_pinky_pinch.enabled` | `pinch.enable_thumb_pinky_pinch` |
| `gestures.thumb_pinky_pinch.cooldown` | 미지원: 핀치는 한 번 집을 때 한 번 실행; 손을 떼어 다시 시작하며 시간 쿨다운을 사용하지 않음 |
| `gestures.thumb_tucked_open.enabled` | `pinch.enable_thumb_tucked_open` |
| `gestures.thumb_tucked_open.cooldown` | `pinch.desktop_cooldown` |

`gestures.<id>.action`은 기본 바인딩·사용자 바인딩 파일로 대체한다. 고급 설정은 바인딩 편집기가 아니다.
`motion_actions`도 `motion.swipe_* → nav.* / tab.*` 바인딩이다.

## PR에 남기는 미대응 항목

구현 없는 스위치를 추가하지 않는다. 필요성은 실험 후 Sean에게 제안한다.

`click_dominance_ratio`, `cursor_interpolation`, `fling_gain_x`, `fling_gain_y`, `fling_strength`, `record_fling_enabled`, `record_gain_max`, `record_gain_min`, `record_min_length`, `record_release_trim`, `record_smoothing`, `record_speed_high`, `record_speed_low`, `replay_max_duration`, `replay_min_duration`, `scroll_center_cursor`, `scroll_center_fallback_window`, `scroll_center_max_fraction`, `scroll_center_tolerance`, `scroll_inertia_seamless`, `scroll_series_dwell`, `scroll_series_gap`, `scroll_session_hold`, `scroll_target_uia`, `scroll_target_wait`, `start_in_tray`, `switcher_confirm_delay`, `switcher_fist_multiplier`, `switcher_lost_confirm`, `switcher_navigation`, `switcher_step`, `switcher_timeout`, `tremor_beta`, `tremor_deadzone`, `tremor_filter`, `tremor_min_cutoff`, `ui_magnet_enabled`, `ui_magnet_max_size`, `ui_magnet_min_factor`, `ui_magnet_radius`.

위 항목과 핀치 시간 쿨다운은 기존 기능과 동등하지 않다. 이 표로 동작 삭제나 기본 꺼짐을 결정하지 않는다.

## 검증 한계

합성 시험은 잘못된 편집 거부·프로필 보호·출력 설정 연결을 검사한다. 실제 웹캠·Windows 입력 감도는 실사용 확인이 필요하다.
