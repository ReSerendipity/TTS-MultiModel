/* ===== App Initialization Module ===== */
(function() {
// Initial tab load with correct language parameter - restore from localStorage if available
document.addEventListener('DOMContentLoaded', function() {
    var tabContent = document.getElementById('tab-content');
    if (tabContent && window.CURRENT_LANG) {
        // URL query 参数优先级最高，其次是 localStorage，最后是默认值
        var urlParams = new URLSearchParams(window.location.search);
        var urlTab = urlParams.get('tab');
        var savedTab = localStorage.getItem('app_current_tab');
        var savedModel = localStorage.getItem('app_current_model');
        var targetTab = urlTab || savedTab || 'voice_design';

        // Load saved or default tab
        htmx.ajax('GET', '/tab/' + targetTab + '?lang=' + window.CURRENT_LANG, {
            target: '#tab-content',
            swap: 'innerHTML'
        });

        // Restore sidebar active state
        if (targetTab) {
            var sidebarItem = document.querySelector('.sidebar-item[data-tab="' + targetTab + '"]');
            if (sidebarItem) {
                document.querySelectorAll('.sidebar-item').forEach(function(s) {
                    s.classList.remove('active');
                });
                sidebarItem.classList.add('active');
            }
            // 如果是 URL 传参，更新 localStorage
            if (urlTab) {
                localStorage.setItem('app_current_tab', urlTab);
            }
        }

        // Restore model selection state - delay to ensure page is fully loaded
        if (savedModel && savedModel !== 'all') {
            setTimeout(function() {
                if (window.switchModel) {
                    window.switchModel(savedModel);
                }
            }, 1000);
        }
    }
});

// ===== HTMX Global Error Handling =====
// Prevent "Cannot read properties of null (reading 'insertBefore')" errors
document.addEventListener('DOMContentLoaded', function() {
    // Ensure #tab-content always exists in DOM
    var ensureTabContent = function() {
        var tabContent = document.getElementById('tab-content');
        if (!tabContent) {
            var mainInner = document.querySelector('.main-content-inner');
            if (mainInner) {
                tabContent = document.createElement('div');
                tabContent.id = 'tab-content';
                tabContent.setAttribute('role', 'tabpanel');
                tabContent.setAttribute('aria-label', '内容区域');
                mainInner.appendChild(tabContent);
                console.warn('[HTMX] Recreated missing #tab-content element');
            }
        }
        return tabContent;
    };

    // Handle HTMX swap errors (e.g. insertBefore on null)
    document.body.addEventListener('htmx:error', function(evt) {
        console.warn('[HTMX] Swap error handled:', evt.detail);
        ensureTabContent();
    });

    // Handle HTMX response errors
    document.body.addEventListener('htmx:responseError', function(evt) {
        console.warn('[HTMX] Response error:', evt.detail.xhr ? evt.detail.xhr.status : 'unknown');
        // Reset generating state on error to re-enable generate buttons
        if (typeof window._setGeneratingState === 'function') {
            window._setGeneratingState(false);
        }
    });

    // Validate target exists in DOM before swap - prevent insertBefore on null
    document.body.addEventListener('htmx:beforeSwap', function(evt) {
        var target = evt.detail.target;
        if (!target || !document.body.contains(target)) {
            console.warn('[HTMX] Target element not in DOM, skipping swap');
            evt.detail.shouldSwap = false;
            // Try to recover by ensuring tab-content exists
            if (evt.detail.pathInfo && evt.detail.pathInfo.requestPath && evt.detail.pathInfo.requestPath.indexOf('/tab/') !== -1) {
                var tabContent = ensureTabContent();
                if (tabContent) {
                    evt.detail.target = tabContent;
                    evt.detail.shouldSwap = true;
                }
            }
            return;
        }
    });

    // Handle HTMX config request - validate target exists
    document.body.addEventListener('htmx:configRequest', function(evt) {
        var target = evt.detail.target;
        if (target && typeof target === 'string') {
            var targetEl = document.querySelector(target);
            if (!targetEl && target === '#tab-content') {
                ensureTabContent();
            }
        }
    });

    // After any HTMX request completes, verify tab content integrity
    document.body.addEventListener('htmx:afterRequest', function() {
        ensureTabContent();
    });
});

// ===== HTMX Audio State Preservation =====
// Save audio state before tab switch, restore after
var savedAudioState = { src: '', currentTime: 0, title: '', wasPlaying: false };

document.addEventListener('DOMContentLoaded', function() {
    document.body.addEventListener('htmx:beforeSwap', function(evt) {
        var target = evt.detail.target;
        if (target && target.id === 'tab-content') {
            var audioEl = document.getElementById('global-audio-element');
            if (audioEl && audioEl.src) {
                savedAudioState = {
                    src: audioEl.src,
                    currentTime: audioEl.currentTime,
                    title: document.getElementById('gap-title').textContent,
                    wasPlaying: !audioEl.paused
                };
                console.log('[HTMX Audio] State saved before swap:', savedAudioState.src, 'playing:', savedAudioState.wasPlaying);
            }
        }
    });

    document.body.addEventListener('htmx:afterSettle', function() {
        if (savedAudioState.src) {
            console.log('[HTMX Audio] Restoring state after settle:', savedAudioState.src);
            var audioEl = document.getElementById('global-audio-element');
            var playerEl = document.getElementById('global-audio-player');

            if (!audioEl || !playerEl) {
                console.warn('[HTMX Audio] Player elements not found, retrying...');
                setTimeout(function() {
                    if (savedAudioState.src) restoreAudioState();
                }, 100);
                return;
            }

            restoreAudioState();
        }
    });
});

function restoreAudioState() {
    var audioEl = document.getElementById('global-audio-element');
    var playerEl = document.getElementById('global-audio-player');
    var titleEl = document.getElementById('gap-title');

    if (!audioEl || !playerEl || !savedAudioState.src) return;

    // Show the player
    playerEl.classList.add('visible');
    var mainContent = document.querySelector('.main-content');
    if (mainContent) mainContent.classList.add('has-audio');

    // Restore audio source if needed
    if (audioEl.src !== savedAudioState.src) {
        audioEl.src = savedAudioState.src;
    }

    // Restore title
    if (titleEl) titleEl.textContent = savedAudioState.title || '播放中...';

    // Restore playback position
    try {
        audioEl.currentTime = savedAudioState.currentTime;
    } catch(e) {
        console.warn('[HTMX Audio] Could not set currentTime:', e);
    }

    // Resume playing if it was playing before
    if (savedAudioState.wasPlaying) {
        audioEl.play().then(function() {
            var playBtn = document.getElementById('gap-play-btn');
            if (playBtn) {
                playBtn.innerHTML = '\u275A\u275A';
                playBtn.classList.add('playing');
            }
        }).catch(function(e) {
            console.warn('[HTMX Audio] Resume play failed:', e);
        });
    }

    console.log('[HTMX Audio] State restored successfully');
    savedAudioState = { src: '', currentTime: 0, title: '', wasPlaying: false };
}

// ===== Tab Switch Transition Animation =====
// Add tab-switching class on sidebar item click (fade out)
document.addEventListener('DOMContentLoaded', function() {
    document.querySelector('.sidebar-nav').addEventListener('click', function(e) {
        var item = e.target.closest('.sidebar-item[data-tab]');
        if (item) {
            var tabContent = document.getElementById('tab-content');
            if (tabContent) {
                tabContent.classList.remove('tab-enter');
                tabContent.classList.add('tab-switching');
            }
        }
    });

    // After HTMX swaps new content, remove tab-switching and add tab-enter (fade in)
    document.body.addEventListener('htmx:afterSwap', function(evt) {
        var target = evt.detail.target;
        if (target && target.id === 'tab-content') {
            target.classList.remove('tab-switching');
            target.classList.add('tab-enter');

            // Clean up tab-enter class after animation completes
            target.addEventListener('animationend', function handler() {
                target.classList.remove('tab-enter');
                target.removeEventListener('animationend', handler);
            });
        }
    });

    // ===== Sync sidebar active state with loaded content =====
    function syncSidebarActiveState(tabId) {
        if (!tabId) return;
        var allItems = document.querySelectorAll('.sidebar-item[data-tab]');
        var matchedItem = null;
        allItems.forEach(function(item) {
            var isActive = item.getAttribute('data-tab') === tabId;
            item.classList.toggle('active', isActive);
            if (isActive) matchedItem = item;
        });
        // Save to localStorage
        localStorage.setItem('app_current_tab', tabId);
        // Update page title
        var pageTitle = document.getElementById('top-page-title');
        if (pageTitle && window.getPageTitles) {
            pageTitle.textContent = window.getPageTitles()[tabId] || 'TTS MultiModel';
        }
    }

    // Listen for HTMX requests to tab endpoints to sync active state
    document.body.addEventListener('htmx:beforeRequest', function(evt) {
        var path = evt.detail.pathInfo ? evt.detail.pathInfo.requestPath : '';
        if (path && path.indexOf('/tab/') !== -1) {
            var tabId = path.split('/tab/')[1].split('?')[0];
            syncSidebarActiveState(tabId);
        }
    });

    // Also sync after successful swap as a fallback
    document.body.addEventListener('htmx:afterSettle', function(evt) {
        var target = evt.detail.target;
        if (target && target.id === 'tab-content') {
            var path = evt.detail.pathInfo ? evt.detail.pathInfo.requestPath : '';
            if (path && path.indexOf('/tab/') !== -1) {
                var tabId = path.split('/tab/')[1].split('?')[0];
                syncSidebarActiveState(tabId);
            }
        }
    });
});

// Auto-switch tab for engine
window.autoSwitchTabForEngine = function(engine) {
    var isVox = engine.toLowerCase().indexOf('voxcpm2') !== -1;
    var activeItem = document.querySelector('.sidebar-item.active');
    if (!activeItem) return;
    var activeEngine = activeItem.getAttribute('data-engine') || '';
    var isActiveVisible = false;
    if (activeEngine === 'all') {
        isActiveVisible = true;
    } else if (isVox && activeEngine === 'voxcpm2') {
        isActiveVisible = true;
    }
    if (!isActiveVisible) {
        var defaultTab = isVox ? 'voice_design' : 'indextts2_clone';
        var targetItem = document.querySelector('.sidebar-item[data-tab="' + defaultTab + '"]');
        if (targetItem) {
            targetItem.click();
        }
    }
};

// ===== Anchor Link Scroll Offset Handling =====
document.addEventListener('DOMContentLoaded', function() {
    function getScrollOffset() {
        var topBar = document.querySelector('.top-bar');
        var progressContainer = document.querySelector('.progress-container.active');
        var formatSelector = document.querySelector('.format-selector');
        var offset = 20; // Base padding
        
        if (topBar) offset += topBar.offsetHeight;
        if (formatSelector) offset += formatSelector.offsetHeight;
        if (progressContainer) offset += progressContainer.offsetHeight;
        
        return offset;
    }
    
    function scrollToAnchor(anchor) {
        if (!anchor) return;
        var element = document.getElementById(anchor);
        if (!element) {
            // Try finding in tab content
            var tabContent = document.getElementById('tab-content');
            if (tabContent) {
                element = tabContent.querySelector('#' + anchor);
            }
        }
        if (!element) return;
        
        var tabContent = document.getElementById('tab-content');
        var scrollContainer = tabContent || document.documentElement;
        
        var elementRect = element.getBoundingClientRect();
        var containerRect = scrollContainer.getBoundingClientRect ? scrollContainer.getBoundingClientRect() : { top: 0 };
        
        var currentScrollTop = scrollContainer === document.documentElement ? window.pageYOffset : scrollContainer.scrollTop;
        var targetY = currentScrollTop + elementRect.top - containerRect.top - getScrollOffset();
        
        scrollContainer.scrollTo({
            top: Math.max(0, targetY),
            behavior: 'smooth'
        });
    }
    
    // Handle anchor clicks
    document.body.addEventListener('click', function(e) {
        var link = e.target.closest('a[href^="#"]');
        if (!link) return;
        
        var href = link.getAttribute('href');
        if (!href || href === '#') return;
        
        var anchor = href.substring(1);
        if (anchor === 'main-content') return; // Skip skip link
        
        e.preventDefault();
        scrollToAnchor(anchor);
        
        // Update URL hash without jumping
        history.pushState(null, '', '#' + anchor);
    });
    
    // Handle initial hash on page load
    if (window.location.hash) {
        var initialAnchor = window.location.hash.substring(1);
        setTimeout(function() {
            scrollToAnchor(initialAnchor);
        }, 500); // Wait for content to load
    }
    
    // Handle hash after HTMX content loads
    document.body.addEventListener('htmx:afterSettle', function() {
        if (window.location.hash) {
            var anchor = window.location.hash.substring(1);
            setTimeout(function() {
                scrollToAnchor(anchor);
            }, 100);
        }
        // Sync generate button disabled state after new tab content loads
        if (typeof window._syncGenerateButtons === 'function') {
            setTimeout(window._syncGenerateButtons, 50);
        }
    });
});

// Expose module API
// @deprecated 使用 TTSApp.init 替代，此 window 挂载点将在未来版本移除
window.AppInit = {
    restoreAudioState: restoreAudioState
};
})();
