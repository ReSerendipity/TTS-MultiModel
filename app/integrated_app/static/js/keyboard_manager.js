/* ===== KeyboardManager: Global Keyboard Shortcut Manager ===== */
(function() {
function KeyboardManager() {
    this.shortcuts = {};
    this.listener = null;
}

KeyboardManager.prototype.register = function(id, keys, description, handler, category) {
    var keyArray = Array.isArray(keys) ? keys : [keys];
    this.shortcuts[id] = {
        id: id,
        keys: keyArray,
        description: description || '',
        handler: handler,
        category: category || 'general'
    };
    console.log('[KeyboardManager] Registered:', id, keyArray.join('+'));
};

KeyboardManager.prototype.unregister = function(id) {
    if (this.shortcuts[id]) {
        delete this.shortcuts[id];
        console.log('[KeyboardManager] Unregistered:', id);
    }
};

KeyboardManager.prototype.getShortcuts = function() {
    var result = [];
    var ids = Object.keys(this.shortcuts);
    for (var i = 0; i < ids.length; i++) {
        var s = this.shortcuts[ids[i]];
        result.push({
            id: s.id,
            keys: s.keys.slice(),
            description: s.description,
            category: s.category
        });
    }
    return result;
};

KeyboardManager.prototype.isTyping = function(event) {
    var tag = event.target.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || event.target.isContentEditable;
};

KeyboardManager.prototype.handleKeydown = function(event) {
    if (this.isTyping(event)) {
        return;
    }

    var ids = Object.keys(this.shortcuts);
    for (var i = 0; i < ids.length; i++) {
        var shortcut = this.shortcuts[ids[i]];
        if (this._matches(shortcut.keys, event)) {
            event.preventDefault();
            event.stopPropagation();
            shortcut.handler(event);
            return;
        }
    }
};

KeyboardManager.prototype._matches = function(keys, event) {
    for (var i = 0; i < keys.length; i++) {
        var key = keys[i];
        var pressed = false;

        if (key === 'Control') {
            pressed = event.ctrlKey;
        } else if (key === 'Shift') {
            pressed = event.shiftKey;
        } else if (key === 'Alt') {
            pressed = event.altKey;
        } else if (key === 'Meta') {
            pressed = event.metaKey;
        } else if (key === 'F1') {
            pressed = event.key === 'F1';
        } else if (key === 'F2') {
            pressed = event.key === 'F2';
        } else if (key === 'F3') {
            pressed = event.key === 'F3';
        } else if (key === 'F4') {
            pressed = event.key === 'F4';
        } else if (key === 'F5') {
            pressed = event.key === 'F5';
        } else if (key === 'F6') {
            pressed = event.key === 'F6';
        } else if (key === 'F7') {
            pressed = event.key === 'F7';
        } else if (key === 'F8') {
            pressed = event.key === 'F8';
        } else if (key === 'F9') {
            pressed = event.key === 'F9';
        } else if (key === 'F10') {
            pressed = event.key === 'F10';
        } else if (key === 'F11') {
            pressed = event.key === 'F11';
        } else if (key === 'F12') {
            pressed = event.key === 'F12';
        } else {
            pressed = event.key.toUpperCase() === key.toUpperCase();
        }

        if (!pressed) {
            return false;
        }
    }

    // Check that no extra modifiers are pressed
    if (event.ctrlKey && !this._hasModifier(keys, 'Control')) return false;
    if (event.shiftKey && !this._hasModifier(keys, 'Shift')) return false;
    if (event.altKey && !this._hasModifier(keys, 'Alt')) return false;
    if (event.metaKey && !this._hasModifier(keys, 'Meta')) return false;

    return true;
};

KeyboardManager.prototype._hasModifier = function(keys, modifier) {
    for (var i = 0; i < keys.length; i++) {
        if (keys[i] === modifier) return true;
    }
    return false;
};

// Mount to window object
// @deprecated 使用 TTSApp.keyboard 替代，此 window 挂载点将在未来版本移除
window.keyboardManager = new KeyboardManager();

// Expose module API
// @deprecated 使用 TTSApp.keyboard 替代，此 window 挂载点将在未来版本移除
window.KeyboardManager = KeyboardManager;
})();
