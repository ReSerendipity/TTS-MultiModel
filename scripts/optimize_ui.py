"""UI/UX Optimization Script for TTS MultiModel"""

path = r"c:\Users\HONOR\TTS_MultiModel\bin\integrated_app\static\css\prototype_v4.css"

with open(path, encoding="utf-8") as f:
    content = f.read()

changes = 0

# 1. Optimize generate button - reduce shadow
old = """    box-shadow: 0 6px 20px rgba(124, 92, 191, 0.35),
                0 2px 6px rgba(124, 92, 191, 0.2),
                inset 0 1px 0 rgba(255, 255, 255, 0.15) !important;
    transition: all 0.25s var(--ease-standard, ease) !important;"""
new = """    box-shadow: 0 4px 14px rgba(124, 92, 191, 0.22),
                0 2px 4px rgba(124, 92, 191, 0.12),
                inset 0 1px 0 rgba(255, 255, 255, 0.12) !important;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;"""
if old in content:
    content = content.replace(old, new)
    changes += 1
    print("1. Button shadow optimized")

# 2. Optimize button padding
old = """    padding: 16px !important;
    font-size: 15px !important;
    font-weight: 700 !important;"""
new = """    padding: 14px 20px !important;
    font-size: 14px !important;
    font-weight: 600 !important;"""
if old in content:
    content = content.replace(old, new)
    changes += 1
    print("2. Button padding optimized")

# 3. Optimize tag active glow
old = """    box-shadow: 0 0 0 3px rgba(124, 92, 191, .2),
                0 4px 12px rgba(124, 92, 191, .3),
                inset 0 1px 0 rgba(255, 255, 255, .15) !important;"""
new = """    box-shadow: 0 0 0 2px rgba(124, 92, 191, .15),
                0 3px 10px rgba(124, 92, 191, .25),
                inset 0 1px 0 rgba(255, 255, 255, .12) !important;"""
if old in content:
    content = content.replace(old, new)
    changes += 1
    print("3. Tag glow optimized")

# 4. Optimize card shadow
old = """    box-shadow: var(--shadow-md, 0 4px 12px rgba(0, 0, 0, .08));
    overflow: hidden;
    transition: box-shadow .2s var(--ease-standard, ease);"""
new = """    box-shadow: 0 1px 3px rgba(0, 0, 0, .04), 0 1px 2px rgba(0, 0, 0, .03);
    overflow: hidden;
    transition: box-shadow .2s ease, transform .2s ease;"""
if old in content:
    content = content.replace(old, new)
    changes += 1
    print("4. Card shadow optimized")

# 5. Optimize card hover
old = """.pv-theme .card:hover {
    box-shadow: var(--shadow-lg, 0 8px 24px rgba(0, 0, 0, .12));
}"""
new = """.pv-theme .card:hover {
    box-shadow: 0 4px 12px rgba(0, 0, 0, .06), 0 2px 4px rgba(0, 0, 0, .04);
}"""
if old in content:
    content = content.replace(old, new)
    changes += 1
    print("5. Card hover optimized")

# Add enhancement styles at the end
enhancements = """

/* ============================================================
   UI/UX ENHANCEMENTS
   ============================================================ */

/* Layout - Better two-column ratio */
.pv-theme .two-col { gap: 16px !important; align-items: flex-start; }
.pv-theme .two-col .col-form { flex: 0 0 62%; max-width: 62%; }
.pv-theme .two-col .col-result { flex: 0 0 calc(38% - 16px); max-width: calc(38% - 16px); position: sticky; top: 64px; max-height: calc(100vh - 140px); overflow-y: auto; padding-right: 4px; scrollbar-width: thin; }

/* Button interactions */
.pv-theme .btn-generate:hover { transform: translateY(-1px); filter: brightness(1.05); }
.pv-theme .btn-generate:active { transform: translateY(0); }
.pv-theme .btn-o { transition: all .15s ease !important; }
.pv-theme .btn-o:hover { transform: translateY(-1px); }

/* Tag interactions */
.pv-theme .tag { transition: all .2s ease !important; }
.pv-theme .tag:hover:not(.active) { transform: translateY(-1px); }
.pv-theme .tag.active { transform: translateY(-1px); }

/* Subtab improvements */
.pv-theme .stabs { background: var(--bg-tertiary, #f3f4f6) !important; padding: 4px !important; border-radius: 8px !important; gap: 4px !important; border-bottom: none !important; margin-bottom: 16px !important; }
.pv-theme .stab { padding: 8px 16px !important; border-radius: 6px !important; border: none !important; transition: all .2s ease !important; }
.pv-theme .stab:hover { background: var(--bg-card, #fff); }
.pv-theme .stab.active { background: var(--bg-card, #fff) !important; box-shadow: 0 1px 3px rgba(0,0,0,0.08) !important; border-bottom: none !important; }

/* Collapsible improvements */
.pv-theme .collapse-body.open { padding: 14px !important; background: var(--bg-tertiary, #f3f4f6); border-radius: 0 0 8px 8px; border: 1px solid var(--border-subtle, #e5e7eb); border-top: none; }

/* Empty state */
.pv-theme .empty-state { padding: 40px 20px !important; background: linear-gradient(180deg, var(--bg-tertiary, #f3f4f6) 0%, transparent 100%); border-radius: 12px; border: 2px dashed var(--border-subtle, #e5e7eb); margin: 8px 0; }
.pv-theme .empty-state svg { width: 72px !important; height: 44px !important; margin-bottom: 16px !important; opacity: .3 !important; }

/* Progress bar */
.pv-theme .progress-bar { height: 6px !important; border-radius: 3px !important; }
.pv-theme .progress-fill { border-radius: 3px !important; transition: width .4s cubic-bezier(0.4,0,0.2,1); position: relative; overflow: hidden; }
.pv-theme .progress-fill::after { content: ''; position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: linear-gradient(90deg, transparent, rgba(255,255,255,0.2), transparent); animation: progressShine 2s infinite; }
@keyframes progressShine { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }

/* Hint text */
.pv-theme .hint-text { border-left: 3px solid var(--accent-primary, #7c5cbf); padding: 10px 14px !important; line-height: 1.6; }

/* Form focus */
.pv-theme .fi:focus, .pv-theme .fs:focus, .pv-theme .ft:focus { box-shadow: 0 0 0 3px rgba(124,92,191,0.12), 0 1px 2px rgba(124,92,191,0.08) !important; }

/* Card padding */
.pv-theme .card-body { padding: 16px !important; }
.pv-theme .card-hdr { padding: 12px 16px !important; }
.pv-theme .fg { margin-bottom: 16px !important; }

/* Top bar glass */
.top-bar { backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); }

/* Card entrance animation */
.pv-theme .card { animation: cardSlideIn .3s ease; }
@keyframes cardSlideIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }

/* Dark mode refinements */
html.dark .pv-theme .card:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.3), 0 2px 4px rgba(0,0,0,0.2) !important; }
html.dark .pv-theme .stabs { background: var(--bg-tertiary, #1f2330) !important; }
html.dark .pv-theme .collapse-body.open { background: var(--bg-tertiary, #1f2330); }
html.dark .pv-theme .empty-state { background: linear-gradient(180deg, rgba(255,255,255,0.02) 0%, transparent 100%); }

/* Responsive */
@media (max-width: 1200px) {
  .pv-theme .two-col .col-form { flex: 0 0 58%; max-width: 58%; }
  .pv-theme .two-col .col-result { flex: 0 0 calc(42% - 16px); max-width: calc(42% - 16px); position: static; max-height: none; }
}
@media (max-width: 900px) {
  .pv-theme .two-col { flex-direction: column !important; }
  .pv-theme .two-col .col-form, .pv-theme .two-col .col-result { flex: 1 1 100% !important; max-width: 100% !important; position: static !important; max-height: none !important; }
  .pv-theme .form-row { grid-template-columns: 1fr !important; }
}
@media (max-width: 600px) {
  .pv-theme .card-body { padding: 12px !important; }
  .pv-theme .card-hdr { padding: 10px 12px !important; }
}

/* Focus visibility */
*:focus-visible { outline: 2px solid var(--accent-primary, #7c5cbf); outline-offset: 2px; }
button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible { outline: 2px solid var(--accent-primary, #7c5cbf); outline-offset: 1px; }

/* Selection */
::selection { background: rgba(124,92,191,0.2); }
"""

content += enhancements

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print(f"\nTotal changes applied: {changes}")
print("UI enhancements added successfully!")
