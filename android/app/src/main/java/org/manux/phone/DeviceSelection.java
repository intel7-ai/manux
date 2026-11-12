package org.manux.phone;

import java.util.List;

/** Index zero is always a prompt, never a paired device. Class values follow Bluetooth CoD. */
final class DeviceSelection {
    static final int COMPUTER = 0x100;
    static final int AUDIO_VIDEO = 0x400;

    static boolean allowed(int majorClass) { return majorClass != AUDIO_VIDEO; }
    static int rank(int majorClass) { return majorClass == COMPUTER ? 0 : 1; }
    static int deviceIndex(int pickerPosition, int deviceCount) {
        int index = pickerPosition - 1;
        return index >= 0 && index < deviceCount ? index : -1;
    }
    static int restorePosition(List<String> addresses, String previousSelection) {
        if (previousSelection == null) return 0;
        int found = addresses.indexOf(previousSelection);
        return found < 0 ? 0 : found + 1;
    }
    private DeviceSelection() { }
}
