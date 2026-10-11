package com.limelight.ui;

import android.content.Context;
import android.text.Editable;
import android.view.KeyEvent;
import android.widget.FrameLayout;

import androidx.annotation.NonNull;

public class ExternalControllerView extends FrameLayout {
    private InputCallbacks inputCallbacks;

    // When enabled, we expose an InputConnection so that soft keyboards can send
    // commitText() events (e.g. swipe typing). Default disabled.
    private boolean commitTextEnabled = false;

    public void setInputCallbacks(InputCallbacks callbacks) {
        this.inputCallbacks = callbacks;
    }

    public void setCommitTextEnabled(boolean enabled) {
        this.commitTextEnabled = enabled;
        // Request focus so that IME targets this view when enabled
        if (enabled) {
            setFocusableInTouchMode(true);
            requestFocus();
        }
    }

    public ExternalControllerView(@NonNull Context context) {
        super(context);
    }

    @Override
    public boolean onKeyPreIme(int keyCode, KeyEvent event) {
        // This callbacks allows us to override dumb IME behavior like when
        // Samsung's default keyboard consumes Shift+Space.
        if (inputCallbacks != null) {
            if (event.getAction() == KeyEvent.ACTION_DOWN) {
                if (inputCallbacks.handleKeyDown(event)) {
                    return true;
                }
            }
            else if (event.getAction() == KeyEvent.ACTION_UP) {
                if (inputCallbacks.handleKeyUp(event)) {
                    return true;
                }
            }
        }

        return super.onKeyPreIme(keyCode, event);
    }

    @Override
    public boolean onCheckIsTextEditor() {
        return commitTextEnabled || super.onCheckIsTextEditor();
    }

    @Override
    public android.view.inputmethod.InputConnection onCreateInputConnection(android.view.inputmethod.EditorInfo outAttrs) {
        if (!commitTextEnabled) {
            return super.onCreateInputConnection(outAttrs);
        }

        // Basic text editor flags – we don't need extract UI or enter action
        outAttrs.inputType = android.text.InputType.TYPE_CLASS_TEXT;
        outAttrs.imeOptions = android.view.inputmethod.EditorInfo.IME_FLAG_NO_EXTRACT_UI;

        return new android.view.inputmethod.BaseInputConnection(this, false) {
            @Override
            public boolean commitText(CharSequence text, int newCursorPosition) {
                if (inputCallbacks != null && inputCallbacks.handleCommitText(text)) {
                    // Discard the base connection's composing copy once the
                    // ordered stream input queue accepts the committed text.
                    getEditable().clear();
                    return true;
                }
                return super.commitText(text, newCursorPosition);
            }

            @Override
            public boolean finishComposingText() {
                Editable editable = getEditable();
                if (editable != null && editable.length() > 0 && inputCallbacks != null &&
                        inputCallbacks.handleCommitText(editable.toString())) {
                    editable.clear();
                    return true;
                }
                return super.finishComposingText();
            }

            @Override
            public boolean deleteSurroundingText(int beforeLength, int afterLength) {
                if (inputCallbacks != null && inputCallbacks.handleDeleteSurroundingText(beforeLength, afterLength)) {
                    return true;
                }
                return super.deleteSurroundingText(beforeLength, afterLength);
            }

            @Override
            public boolean sendKeyEvent(KeyEvent event) {
                // Keep IME Enter/editor actions behind pending text and deletes,
                // just as on the main streaming view.
                return inputCallbacks != null && inputCallbacks.handleImeKeyEvent(event) || super.sendKeyEvent(event);
            }
        };
    }

    public interface InputCallbacks {
        boolean handleKeyUp(KeyEvent event);
        boolean handleKeyDown(KeyEvent event);
        boolean handleCommitText(CharSequence text);
        boolean handleDeleteSurroundingText(int beforeLength, int afterLength);
        boolean handleImeKeyEvent(KeyEvent event);
    }
}
