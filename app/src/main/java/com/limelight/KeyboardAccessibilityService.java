package com.limelight;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.AccessibilityServiceInfo;
import android.os.Build;
import android.view.KeyEvent;
import android.view.accessibility.AccessibilityEvent;
import android.widget.Toast;

import com.limelight.utils.ExternalDisplayControlActivity;

import java.util.Arrays;
import java.util.List;

public class KeyboardAccessibilityService extends AccessibilityService {

    //不屏蔽的按键列表
    private final static List BLACKLIST_KEYS = Arrays.asList(
            KeyEvent.KEYCODE_VOLUME_UP,
            KeyEvent.KEYCODE_VOLUME_DOWN,
            KeyEvent.KEYCODE_POWER
    );

    @Override
    public boolean onKeyEvent(KeyEvent event) {
        int action = event.getAction();
        int keyCode = event.getKeyCode();
//        Toast.makeText(getApplicationContext(),"scancode:"+event.getScanCode()+",code:"+event.getKeyCode(),Toast.LENGTH_LONG).show();
        //主要解决系统自带快捷键在pc端无法使用问题 home键 scancode=172 code- 3
        Game g = Game.instance;
        if (g != null && g.connected && streamHasFocus(g) && !BLACKLIST_KEYS.contains(keyCode)) {

            if (action == KeyEvent.ACTION_DOWN) {
                //fix 小米平板esc键按钮映射错误 KEYCODE_BACK=4
                if(event.getScanCode()==1){
                    Game.instance.handleKeyDown(new KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_ESCAPE));
                    return true;
                }
                Game.instance.handleKeyDown(event);
                return true;
            } else if (action == KeyEvent.ACTION_UP) {
                //fix 小米平板esc键按钮映射错误 KEYCODE_BACK=4
                if(event.getScanCode()==1){
                    Game.instance.handleKeyUp(new KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_ESCAPE));
                    return true;
                }
                Game.instance.handleKeyUp(event);
                return true;
            }
        }

        return super.onKeyEvent(event);
    }

    // Only intercept keys while the stream UI is actually in front of the user. The service
    // receives every hardware key pressed anywhere on the device, so without this check keys
    // typed into other apps (stream in the background, split-screen or picture-in-picture)
    // would be swallowed here and forwarded to the host.
    private static boolean streamHasFocus(Game game) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && game.isInPictureInPictureMode()) {
            return false;
        }

        if (game.hasWindowFocus()) {
            return true;
        }

        // The stream may be driven from the external display control activity instead
        ExternalDisplayControlActivity control = ExternalDisplayControlActivity.instance;
        return control != null && control.hasWindowFocus();
    }

    @Override
    public void onServiceConnected() {
        LimeLog.info("Keyboard service is connected");
        AccessibilityServiceInfo info = new AccessibilityServiceInfo();
        info.packageNames = new String[] { BuildConfig.APPLICATION_ID };
        info.eventTypes = AccessibilityEvent.TYPES_ALL_MASK;
        info.notificationTimeout = 100;
        info.flags = AccessibilityServiceInfo.FLAG_REQUEST_FILTER_KEY_EVENTS;
        info.feedbackType = AccessibilityServiceInfo.FEEDBACK_SPOKEN;
        setServiceInfo(info);
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent accessibilityEvent) {
//        LimeLog.info("onAccessibilityEvent:"+accessibilityEvent.toString());
    }
    @Override
    public void onInterrupt() {

    }

}