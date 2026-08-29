/* ===== Sidebar Manager Module ===== */
(function() {
// Page titles map - lazy initialization since window.I18N is set inline after main.js loads
var pageTitles = {};
function getPageTitles() {
    if (Object.keys(pageTitles).length === 0 && window.I18N) {
        pageTitles = {
            voice_design: window.I18N["tab_voice_design"],
            voice_clone: window.I18N["tab_voice_clone"],
            ultimate_clone: window.I18N["tab_ultimate_clone"],
            script: window.I18N["tab_script"],
            prompt_continue: window.I18N["tab_prompt_continue"],
            settings: window.I18N["tab_settings"],
            indextts2_clone: window.I18N["tab_indextts2_clone"],
            indextts2_emotion: window.I18N["tab_indextts2_emotion"],
            indextts2_duration: window.I18N["tab_indextts2_duration"],
            indextts20_clone: window.I18N["tab_indextts20_clone"],
            indextts20_emotion: window.I18N["tab_indextts20_emotion"],
            lora: window.I18N["tab_lora"],
            lora_training: window.I18N["tab_lora_training"],
            history: window.I18N["tab_history"],
            persona: window.I18N["tab_persona"],
            help: window.I18N["tab_help"]
        };
    }
    return pageTitles;
}

// Expose getPageTitles globally for other modules (SSE manager uses it)
window.getPageTitles = getPageTitles;

// Activate sidebar items
function activateSidebarTab(btn) {
    document.querySelectorAll('.sidebar-item').forEach(function(s) {
        s.classList.remove('active');
    });
    btn.classList.add('active');
    var tabId = btn.getAttribute('data-tab');
    if (tabId) {
        // Save current tab to localStorage for page refresh restoration
        localStorage.setItem('app_current_tab', tabId);
        localStorage.setItem('app_current_model', btn.getAttribute('data-model') || 'all');

        // Update top page title
        var pageTitle = document.getElementById('top-page-title');
        if (pageTitle && getPageTitles()[tabId]) {
            pageTitle.textContent = getPageTitles()[tabId];
        }

        // Announce tab change to screen readers via ARIA live region
        var ariaLiveEl = document.getElementById('aria-live-status');
        if (ariaLiveEl && getPageTitles()[tabId]) {
            ariaLiveEl.textContent = getPageTitles()[tabId];
        }

        // Show skeleton for history and persona tabs
        if (tabId === 'history' || tabId === 'persona') {
            if (window.SkeletonManager) {
                window.SkeletonManager.showTabSkeleton(tabId);
            }
        }
    }
    // Reset scroll position when switching tabs
    var tabContent = document.getElementById('tab-content');
    if (tabContent) {
        tabContent.scrollTop = 0;
    }
    closeSidebar();
}

// Legacy activateTab - now only handles sidebar activation
// Supports both element (with data-tab attribute) and string (tabId) arguments
function activateTabLegacy(btnOrTabId) {
    var tabId = (typeof btnOrTabId === 'string') ? btnOrTabId : btnOrTabId.getAttribute('data-tab');
    if (tabId) {
        document.querySelectorAll('.sidebar-item').forEach(function(s) {
            s.classList.toggle('active', s.getAttribute('data-tab') === tabId);
        });
        // Update top page title
        var pageTitle = document.getElementById('top-page-title');
        if (pageTitle && getPageTitles()[tabId]) {
            pageTitle.textContent = getPageTitles()[tabId];
        }
    }
}

// Sidebar toggle (mobile/tablet)
function toggleSidebar() {
    var sidebar = document.getElementById('sidebar');
    var overlay = document.getElementById('sidebar-overlay');
    if (!sidebar || !overlay) return;

    var isOverlayMode = window.matchMedia('(max-width: 1200px)').matches;
    if (!isOverlayMode) return; // Never use overlay in desktop mode

    var isOpen = sidebar.classList.contains('open');
    if (isOpen) {
        closeSidebar();
    } else {
        openMobileSidebar();
    }
}

function openMobileSidebar() {
    var sidebar = document.getElementById('sidebar');
    var overlay = document.getElementById('sidebar-overlay');
    if (!sidebar || !overlay) return;
    sidebar.classList.add('open');
    document.body.classList.add('sidebar-open');
    // Force inline styles to override any CSS cascade issues
    sidebar.style.transform = 'translateX(0)';
    sidebar.style.width = 'var(--sidebar-width)';
    sidebar.style.minWidth = 'var(--sidebar-width)';
    sidebar.style.overflow = 'visible';
    // Use rAF to ensure CSS transition triggers properly
    requestAnimationFrame(function() {
        overlay.classList.add('visible', 'active');
        overlay.style.display = 'block';
        overlay.style.opacity = '1';
        overlay.style.pointerEvents = 'auto';
    });
}

function closeSidebar() {
    var sidebar = document.getElementById('sidebar');
    var overlay = document.getElementById('sidebar-overlay');
    var isOverlayMode = window.matchMedia('(max-width: 1200px)').matches;

    if (sidebar) {
        sidebar.classList.remove('open');
        document.body.classList.remove('sidebar-open');
        if (isOverlayMode) {
            // Only slide out in overlay/mobile mode
            sidebar.style.transform = 'translateX(calc(-1 * var(--sidebar-width)))';
        } else {
            // Desktop mode: clear any inline transform from mobile mode
            sidebar.style.transform = '';
            sidebar.style.width = '';
            sidebar.style.minWidth = '';
            sidebar.style.overflow = '';
        }
    }
    if (overlay) {
        overlay.classList.remove('visible');
        overlay.classList.remove('active');
        overlay.style.opacity = '0';
        overlay.style.pointerEvents = 'none';
        if (isOverlayMode) {
            // After transition completes, reset inline styles
            setTimeout(function() {
                if (sidebar && !sidebar.classList.contains('open')) {
                    sidebar.style.width = '';
                    sidebar.style.minWidth = '';
                    sidebar.style.overflow = '';
                    if (!isOverlayMode) {
                        sidebar.style.transform = '';
                    }
                }
                if (!overlay.classList.contains('visible') && !overlay.classList.contains('active')) {
                    overlay.style.display = '';
                }
            }, 350);
        } else {
            overlay.style.display = 'none';
        }
    }
}
window.toggleSidebar = toggleSidebar;
window.openMobileSidebar = openMobileSidebar;
window.closeSidebar = closeSidebar;

// ============================================================
// Sidebar Toggle
// ============================================================
function toggleSidebarCollapse() {
    var sidebar = document.querySelector('.sidebar');
    var toggleBtn = document.getElementById('sidebar-toggle-btn');
    var toggleIcon = document.getElementById('sidebar-toggle-icon');
    var edgeToggle = document.getElementById('sidebar-edge-toggle');
    if (!sidebar) return;

    var isCollapsed = sidebar.classList.contains('collapsed');
    var prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    if (prefersReducedMotion) {
        // 直接切换，不播放动画
        sidebar.classList.toggle('collapsed');
        isCollapsed = sidebar.classList.contains('collapsed');
    } else if (isCollapsed) {
        // 展开：先移除 collapsed（触发宽度展开），添加 expanding
        sidebar.classList.remove('collapsed');
        sidebar.classList.add('expanding');
        // 200ms 后添加 expanded（触发内容渐显），然后清理
        setTimeout(function() {
            sidebar.classList.add('expanded');
            setTimeout(function() {
                sidebar.classList.remove('expanding');
                sidebar.classList.remove('expanded');
            }, 100);
        }, 200);
        isCollapsed = false;
    } else {
        // 折叠：先添加 collapsing（触发内容渐隐），100ms 后添加 collapsed（触发宽度折叠）
        sidebar.classList.add('collapsing');
        setTimeout(function() {
            sidebar.classList.add('collapsed');
            sidebar.classList.remove('collapsing');
        }, 100);
        isCollapsed = true;
    }

    // Update aria-expanded on toggle buttons and sidebar itself
    var expanded = !isCollapsed;
    sidebar.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    if (toggleBtn) {
        toggleBtn.title = isCollapsed ? window.I18N["expand_sidebar"] : window.I18N["collapse_sidebar"];
        toggleBtn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    }
    if (edgeToggle) {
        edgeToggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    }
    if (toggleIcon) {
        toggleIcon.innerHTML = isCollapsed
            ? '<rect x="3" y="5" width="18" height="14" rx="2" ry="2"/><path d="M16 9l3 3-3 3"/><line x1="8" x2="8" y1="5" y2="19"/>'
            : '<rect x="3" y="5" width="18" height="14" rx="2" ry="2"/><path d="M8 9l-3 3 3 3"/><line x1="8" x2="8" y1="5" y2="19"/>';
    }

    // Manage 'open' class and overlay for small/medium screens (<=1200px)
    // where sidebar is position:fixed with transform and only .open brings it into view
    // Only apply overlay behavior when sidebar is in fixed/overlay mode (viewport <= 1200px)
    var isOverlayMode = window.matchMedia('(max-width: 1200px)').matches;
    var overlay = document.getElementById('sidebar-overlay');
    if (isOverlayMode) {
        if (isCollapsed) {
            sidebar.classList.remove('open');
            document.body.classList.remove('sidebar-open');
            sidebar.style.transform = 'translateX(calc(-1 * var(--sidebar-width)))';
            if (overlay) {
                overlay.classList.remove('visible', 'active');
                overlay.style.opacity = '0';
                overlay.style.pointerEvents = 'none';
            }
        } else {
            sidebar.classList.add('open');
            document.body.classList.add('sidebar-open');
            sidebar.style.transform = 'translateX(0)';
            sidebar.style.width = 'var(--sidebar-width)';
            sidebar.style.minWidth = 'var(--sidebar-width)';
            sidebar.style.overflow = 'visible';
            if (overlay) {
                requestAnimationFrame(function() {
                    overlay.classList.add('visible', 'active');
                    overlay.style.display = 'block';
                    overlay.style.opacity = '1';
                    overlay.style.pointerEvents = 'auto';
                });
            }
        }
    } else {
        // Desktop mode: ensure overlay is always hidden and non-blocking
        sidebar.classList.remove('open');
        document.body.classList.remove('sidebar-open');
        sidebar.style.transform = '';
        sidebar.style.width = '';
        sidebar.style.minWidth = '';
        sidebar.style.overflow = '';
        if (overlay) {
            overlay.classList.remove('visible', 'active');
            overlay.style.display = 'none';
            overlay.style.opacity = '0';
            overlay.style.pointerEvents = 'none';
        }
    }

    // Save collapse state to localStorage
    localStorage.setItem('sidebar_collapsed', isCollapsed ? 'true' : 'false');

    // Update audio player position via CSS class on body
    if (isCollapsed) {
        document.body.classList.add('sidebar-is-collapsed');
    } else {
        document.body.classList.remove('sidebar-is-collapsed');
    }

    // Update sidebar items accessibility based on collapse state
    var sidebarItems = document.querySelectorAll('.sidebar-item');
    var sidebarLabels = document.querySelectorAll('.sidebar-nav-label');
    var activeItem = document.activeElement;
    var activeItemWasSidebarItem = activeItem && activeItem.classList && activeItem.classList.contains('sidebar-item');

    sidebarItems.forEach(function(item) {
        if (isCollapsed) {
            item.setAttribute('tabindex', '-1');
            item.setAttribute('aria-hidden', 'true');
        } else {
            item.setAttribute('tabindex', '0');
            item.removeAttribute('aria-hidden');
        }
    });

    sidebarLabels.forEach(function(label) {
        if (isCollapsed) {
            label.setAttribute('aria-hidden', 'true');
        } else {
            label.removeAttribute('aria-hidden');
        }
    });

    // If focus was on a sidebar item when collapsing, move focus to the toggle button
    // so the focus order remains logical and the item is not trapped in an inaccessible element
    if (isCollapsed && activeItemWasSidebarItem) {
        var toggleBtn = document.getElementById('sidebar-toggle-btn');
        if (toggleBtn) toggleBtn.focus();
    }

    // Dispatch resize event so layouts can adapt
    window.dispatchEvent(new Event('resize'));
}

// ============================================================
// Sidebar Tab Activation (global)
// ============================================================
// Supports both element (with data-tab attribute) and string (tabId) arguments
function activateSidebarTabFn(btnOrTabId) {
    // Debounce: ignore clicks while a tab request is already in flight
    if (document.body.classList.contains('htmx-request') || document.querySelector('#tab-content.htmx-request')) {
        return;
    }

    var tabId = (typeof btnOrTabId === 'string') ? btnOrTabId : btnOrTabId.getAttribute('data-tab');
    document.querySelectorAll('.sidebar-item').forEach(function(s) {
        s.classList.toggle('active', s.getAttribute('data-tab') === tabId);
        s.setAttribute('aria-selected', s.getAttribute('data-tab') === tabId ? 'true' : 'false');
    });
    if (tabId) {
        var pageTitle = document.getElementById('top-page-title');
        if (pageTitle && getPageTitles()[tabId]) {
            pageTitle.textContent = getPageTitles()[tabId];
        }
        if (tabId === 'history' || tabId === 'persona') {
            if (window.SkeletonManager) {
                window.SkeletonManager.showTabSkeleton(tabId);
            }
        }
    }
    // Reset scroll position when switching tabs
    var tabContent = document.getElementById('tab-content');
    if (tabContent) {
        tabContent.scrollTop = 0;
    }
    closeSidebar();
}

// Expose module API via TTSApp namespace
TTSApp.sidebar = {
    toggle: toggleSidebar,
    close: closeSidebar,
    toggleCollapse: toggleSidebarCollapse,
    activateTab: activateSidebarTabFn
};

// Reset scroll position when HTMX content swap completes
document.body.addEventListener('htmx:afterSwap', function(event) {
    if (event.detail.target && event.detail.target.id === 'tab-content') {
        event.detail.target.scrollTop = 0;
    }
});

// Initialize sidebar state on load
(function initSidebarState() {
    var sidebar = document.querySelector('.sidebar');
    if (!sidebar) return;

    var isOverlayMode = window.matchMedia('(max-width: 1200px)').matches;
    var savedCollapsed = localStorage.getItem('sidebar_collapsed');
    var isCollapsed = savedCollapsed === 'true';

    // Set initial sidebar state based on viewport and saved preference
    if (isOverlayMode) {
        // Overlay mode (<=1200px): sidebar starts hidden, needs .open to show
        // Default: closed on small screens, unless user preference says otherwise
        sidebar.classList.remove('collapsed');
        if (savedCollapsed === 'false') {
            sidebar.classList.add('open');
        } else {
            sidebar.classList.remove('open');
        }
        isCollapsed = !sidebar.classList.contains('open');
    } else {
        // Desktop mode (>1200px): sidebar uses .collapsed for collapsed state
        sidebar.classList.remove('open');
        if (isCollapsed) {
            sidebar.classList.add('collapsed');
            document.body.classList.add('sidebar-is-collapsed');
        } else {
            sidebar.classList.remove('collapsed');
            document.body.classList.remove('sidebar-is-collapsed');
        }
    }

    // Ensure overlay is in correct state
    var overlay = document.getElementById('sidebar-overlay');
    if (isOverlayMode && !isCollapsed) {
        sidebar.classList.add('open');
        sidebar.style.transform = 'translateX(0)';
        sidebar.style.width = 'var(--sidebar-width)';
        sidebar.style.minWidth = 'var(--sidebar-width)';
        sidebar.style.overflow = 'visible';
        document.body.classList.add('sidebar-open');
        if (overlay) {
            requestAnimationFrame(function() {
                overlay.classList.add('visible', 'active');
                overlay.style.display = 'block';
                overlay.style.opacity = '1';
                overlay.style.pointerEvents = 'auto';
            });
        }
    } else {
        sidebar.classList.remove('open');
        if (isOverlayMode) {
            sidebar.style.transform = 'translateX(calc(-1 * var(--sidebar-width)))';
        } else {
            sidebar.style.transform = '';
            sidebar.style.width = '';
            sidebar.style.minWidth = '';
            sidebar.style.overflow = '';
        }
        document.body.classList.remove('sidebar-open');
        if (overlay) {
            overlay.classList.remove('visible', 'active');
            overlay.style.opacity = '0';
            overlay.style.pointerEvents = 'none';
            if (!isOverlayMode) {
                overlay.style.display = 'none';
            } else {
                overlay.style.display = '';
            }
        }
    }

    // Sync ARIA expanded state on toggle buttons
    var toggleBtn = document.getElementById('sidebar-toggle-btn');
    var edgeToggle = document.getElementById('sidebar-edge-toggle');
    var expanded = !isCollapsed;
    if (toggleBtn) {
        toggleBtn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    }
    if (edgeToggle) {
        edgeToggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    }

    // Sync sidebar's own aria-expanded
    sidebar.setAttribute('aria-expanded', expanded ? 'true' : 'false');

    // Update sidebar items accessibility
    var sidebarItems = document.querySelectorAll('.sidebar-item');
    var sidebarLabels = document.querySelectorAll('.sidebar-nav-label');

    sidebarItems.forEach(function(item) {
        if (isCollapsed) {
            item.setAttribute('tabindex', '-1');
            item.setAttribute('aria-hidden', 'true');
        } else {
            item.setAttribute('tabindex', '0');
            item.removeAttribute('aria-hidden');
        }
    });
    sidebarLabels.forEach(function(label) {
        if (isCollapsed) {
            label.setAttribute('aria-hidden', 'true');
        } else {
            label.removeAttribute('aria-hidden');
        }
    });

    // Update toggle icon
    var toggleIcon = document.getElementById('sidebar-toggle-icon');
    if (toggleIcon) {
        toggleIcon.innerHTML = isCollapsed
            ? '<rect x="3" y="5" width="18" height="14" rx="2" ry="2"/><path d="M16 9l3 3-3 3"/><line x1="8" x2="8" y1="5" y2="19"/>'
            : '<rect x="3" y="5" width="18" height="14" rx="2" ry="2"/><path d="M8 9l-3 3 3 3"/><line x1="8" x2="8" y1="5" y2="19"/>';
    }

    // ===== Collapsible Nav Sections =====
    function initCollapsibleSections() {
        var sections = document.querySelectorAll('.sidebar-nav-section');
        sections.forEach(function(section, index) {
            var label = section.querySelector('.sidebar-nav-label');
            if (!label) return;

            var sectionKey = 'nav_section_' + (index + 1) + '_collapsed';
            var savedState = localStorage.getItem(sectionKey);
            // 新用户默认折叠"高级工具"分组（DOM 顺序索引 1）；用户主动折叠/展开后尊重其选择
            var defaultCollapsed = (index === 1);
            var isSectionCollapsed = savedState === null ? defaultCollapsed : savedState === 'true';

            if (isSectionCollapsed) {
                section.classList.add('section-collapsed');
            }

            label.style.cursor = 'pointer';
            label.setAttribute('role', 'button');
            label.setAttribute('tabindex', '0');
            label.setAttribute('aria-expanded', !isSectionCollapsed);

            var toggleSection = function() {
                isSectionCollapsed = !isSectionCollapsed;
                section.classList.toggle('section-collapsed', isSectionCollapsed);
                label.setAttribute('aria-expanded', !isSectionCollapsed);
                localStorage.setItem(sectionKey, isSectionCollapsed ? 'true' : 'false');
            };

            label.addEventListener('click', function(e) {
                e.stopPropagation();
                toggleSection();
            });

            label.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    e.stopPropagation();
                    toggleSection();
                }
            });

            var chevron = document.createElement('span');
            chevron.className = 'section-collapse-chevron';
            chevron.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';
            label.appendChild(chevron);
        });
    }

    initCollapsibleSections();

    // Handle viewport resize: sync overlay/sidebar state between modes
    var resizeTimeout;
    var wasOverlayMode = isOverlayMode;
    window.addEventListener('resize', function() {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(function() {
            var isOverlayNow = window.matchMedia('(max-width: 1200px)').matches;
            var sidebarEl = document.querySelector('.sidebar');
            var overlayEl = document.getElementById('sidebar-overlay');
            if (!sidebarEl) return;

            if (isOverlayNow !== wasOverlayMode) {
                wasOverlayMode = isOverlayNow;
                if (isOverlayNow) {
                    // Switching TO overlay mode (desktop -> mobile/tablet)
                    sidebarEl.classList.remove('collapsed');
                    var wasExpanded = !document.body.classList.contains('sidebar-is-collapsed');
                    document.body.classList.remove('sidebar-is-collapsed');
                    if (wasExpanded) {
                        sidebarEl.classList.add('open');
                        sidebarEl.style.transform = 'translateX(0)';
                        sidebarEl.style.width = 'var(--sidebar-width)';
                        sidebarEl.style.minWidth = 'var(--sidebar-width)';
                        sidebarEl.style.overflow = 'visible';
                        document.body.classList.add('sidebar-open');
                        if (overlayEl) {
                            requestAnimationFrame(function() {
                                overlayEl.classList.add('visible', 'active');
                                overlayEl.style.display = 'block';
                                overlayEl.style.opacity = '1';
                                overlayEl.style.pointerEvents = 'auto';
                            });
                        }
                    } else {
                        sidebarEl.classList.remove('open');
                        sidebarEl.style.transform = 'translateX(calc(-1 * var(--sidebar-width)))';
                        document.body.classList.remove('sidebar-open');
                        if (overlayEl) {
                            overlayEl.classList.remove('visible', 'active');
                            overlayEl.style.opacity = '0';
                            overlayEl.style.pointerEvents = 'none';
                        }
                    }
                } else {
                    // Switching TO desktop mode (mobile/tablet -> desktop)
                    sidebarEl.classList.remove('open');
                    sidebarEl.style.transform = '';
                    sidebarEl.style.width = '';
                    sidebarEl.style.minWidth = '';
                    sidebarEl.style.overflow = '';
                    document.body.classList.remove('sidebar-open');
                    if (overlayEl) {
                        overlayEl.classList.remove('visible', 'active');
                        overlayEl.style.display = 'none';
                        overlayEl.style.opacity = '0';
                        overlayEl.style.pointerEvents = 'none';
                    }
                    // Restore collapsed state from localStorage
                    var saved = localStorage.getItem('sidebar_collapsed');
                    if (saved === 'true') {
                        sidebarEl.classList.add('collapsed');
                        document.body.classList.add('sidebar-is-collapsed');
                    } else {
                        sidebarEl.classList.remove('collapsed');
                        document.body.classList.remove('sidebar-is-collapsed');
                    }
                }
            }
        }, 150);
    });
})();
})();
