/**
 * MNID dashboard per-chart PNG capture.
 *
 * Injects a custom download button into Plotly's modebar (replacing the
 * built-in one) on every chart that has the modebar enabled.
 * Uses Plotly.toImage() + canvas compositing with a white-themed header.
 */
(function () {
    'use strict';

    // ── Layout constants ────────────────────────────────────────────────────
    var PAD = 14;
    var DIVIDER_Y_GAP = 10;
    var HEADER_WIDTH = 640;

    // Feather camera icon
    var CAMERA_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:block;margin:0 auto;">' +
        '<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>' +
        '<circle cx="12" cy="13" r="4"/>' +
        '</svg>';

    // ── Data helpers ─────────────────────────────────────────────────────────

    function readStoreData() {
        var storeEl = document.getElementById('mnid-capture-store');
        if (!storeEl) return null;
        try {
            var raw = storeEl.getAttribute('data-capture');
            if (!raw) return null;
            return JSON.parse(raw);
        } catch (e) {
            return null;
        }
    }

    function findCategoryInfo(card) {
        var pillsRow = card.querySelector('.mnid-pills');
        if (!pillsRow) return { title: fallbackTitle(card), pills: [] };

        var titleEl = pillsRow.previousElementSibling;
        var title = titleEl ? titleEl.textContent.trim() : '';

        var pills = [];
        var pillEls = pillsRow.querySelectorAll('.mnid-pill');
        for (var i = 0; i < pillEls.length; i++) {
            pills.push({
                text: pillEls[i].textContent.trim(),
                cls: pillEls[i].className
            });
        }

        if (!title) title = fallbackTitle(card);

        return { title: title, pills: pills };
    }

    function fallbackTitle(card) {
        var cardTitle = card.querySelector('.mnid-card-title');
        if (cardTitle) return cardTitle.textContent.trim();

        var gd = card.querySelector('.js-plotly-plot');
        if (gd && gd._fullLayout && gd._fullLayout.title && gd._fullLayout.title.text) {
            var t = gd._fullLayout.title.text;
            if (t && t !== 'Click to enter Plot title') return t;
        }

        var section = card.closest('section, [id]');
        if (section) {
            var sectionLbl = section.querySelector('.mnid-section-lbl');
            if (sectionLbl) return sectionLbl.textContent.trim();
        }

        return 'Chart';
    }

    // ── Canvas drawing ───────────────────────────────────────────────────────

    function textWidth(ctx, text, font) {
        ctx.save();
        ctx.font = font;
        var w = ctx.measureText(text).width;
        ctx.restore();
        return w;
    }

    function drawHeader(ctx, w, data, catInfo) {
        // Calculate total header height first
        var h = PAD + 22;                     // title
        if (catInfo.pills.length > 0) h += 18; // pills
        h += 6;                                // gap
        h += DIVIDER_Y_GAP;                    // divider space
        h += 18 * 2;                           // filter rows
        h += 8;                                // bottom padding

        // White card background
        ctx.fillStyle = '#ffffff';
        ctx.beginPath();
        var r = 12;
        ctx.moveTo(r, 0);
        ctx.lineTo(w - r, 0);
        ctx.quadraticCurveTo(w, 0, w, r);
        ctx.lineTo(w, h);
        ctx.lineTo(0, h);
        ctx.lineTo(0, r);
        ctx.quadraticCurveTo(0, 0, r, 0);
        ctx.closePath();
        ctx.fill();

        // Draw content
        var pos = PAD;

        ctx.fillStyle = '#0F172A';
        ctx.font = 'bold 17px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        ctx.textBaseline = 'top';
        ctx.textAlign = 'left';
        ctx.fillText(catInfo.title, PAD, pos);
        pos += 22;

        var pills = catInfo.pills;
        if (pills.length > 0) {
            var pillX = PAD;
            for (var i = 0; i < pills.length; i++) {
                var fg = '#475569';
                for (var k in PILL_COLORS) {
                    if (pills[i].cls.indexOf(k) !== -1) {
                        fg = PILL_COLORS[k].fg;
                        break;
                    }
                }
                ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
                ctx.fillStyle = fg;
                ctx.fillText(pills[i].text, pillX, pos + 4);
                pillX += textWidth(ctx, pills[i].text, ctx.font) + 14;
            }
            pos += 18;
        }

        pos += 6;

        // Divider line
        ctx.strokeStyle = '#E2E8F0';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(PAD, pos);
        ctx.lineTo(w - PAD, pos);
        ctx.stroke();

        pos += DIVIDER_Y_GAP;

        // Filter context — two-column layout
        ctx.textAlign = 'left';
        var metaFont = '11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        var metaFontBold = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        var labelColor = '#94A3B8';
        var valueColor = '#334155';
        var colW = 220;
        var lineH = 18;

        [ // Row 1: Facility | District
            [
                { label: 'Facility', value: data.facility || 'N/A' },
                { label: 'District', value: data.district || 'N/A' },
            ],
            // Row 2: Period | Program
            [
                { label: 'Period', value: data.period || 'N/A' },
                { label: 'Program', value: data.program || 'N/A' },
            ]
        ].forEach(function (row) {
            var rx = PAD;
            for (var j = 0; j < row.length; j++) {
                ctx.fillStyle = labelColor;
                ctx.font = metaFont;
                ctx.fillText(row[j].label, rx, pos);
                ctx.fillStyle = valueColor;
                ctx.font = metaFontBold;
                ctx.fillText(row[j].value, rx + 52, pos);
                rx += colW;
            }
            pos += lineH;
        });

        return h;
    }

    // Pill colours matching the .mnid-pill-* CSS classes
    var PILL_COLORS = {
        'mnid-pill-green':  { fg: '#16A34A' },
        'mnid-pill-amber':  { fg: '#7A5A00' },
        'mnid-pill-red':    { fg: '#B91C1C' },
        'mnid-pill-blue':   { fg: '#475569' },
    };

    // ── Capture & download ───────────────────────────────────────────────────

    function captureCardByGd(gd) {
        var data = readStoreData();
        if (!data) {
            alert('Cannot download: dashboard filter information not available on this page.');
            return;
        }

        var card = gd.closest('.mnid-chart-card');
        var catInfo = card ? findCategoryInfo(card) : { title: fallbackTitle(gd), pills: [] };

        // Use the actual chart DOM size to preserve aspect ratio
        var domW = gd.offsetWidth || 540;
        var domH = gd.offsetHeight || 300;
        var ratio = domH / domW;

        var plotW = HEADER_WIDTH * 2;
        var plotH = Math.round(plotW * ratio);

        Plotly.toImage(gd, { format: 'png', width: plotW, height: plotH, scale: 1 })
            .then(function (chartDataUrl) {
                var chartImg = new Image();
                chartImg.onload = function () {
                    // Measure exact header height by drawing to a scratch canvas
                    var scratch = document.createElement('canvas');
                    scratch.width = plotW;
                    scratch.height = 2000;
                    var sctx = scratch.getContext('2d');
                    var hdrH = drawHeader(sctx, plotW, data, catInfo);

                    var chartH = Math.round(chartImg.naturalHeight * (plotW / chartImg.naturalWidth));
                    var totalH = hdrH + chartH;

                    var canvas = document.createElement('canvas');
                    canvas.width = plotW;
                    canvas.height = totalH;
                    var ctx = canvas.getContext('2d');

                    ctx.fillStyle = '#F8FAFC';
                    ctx.fillRect(0, 0, plotW, totalH);

                    // Draw header + chart from scratch image
                    ctx.drawImage(scratch, 0, 0);
                    ctx.drawImage(chartImg, 0, hdrH, plotW, chartH);

                    canvas.toBlob(function (blob) {
                        var safeTitle = (catInfo.title || 'chart').replace(/[^a-zA-Z0-9_\- ]/g, '').replace(/\s+/g, '_');
                        var safeFacility = (data.facility || 'report').replace(/[^a-zA-Z0-9_\- ]/g, '').replace(/\s+/g, '_');
                        var url = URL.createObjectURL(blob);
                        var link = document.createElement('a');
                        link.download = safeTitle + '_' + safeFacility + '.png';
                        link.href = url;
                        link.click();
                        setTimeout(function () { URL.revokeObjectURL(url); }, 100);
                    }, 'image/png');
                };
                chartImg.onerror = function () {
                    alert('Failed to load chart image for download.');
                };
                chartImg.src = chartDataUrl;
            })
            .catch(function (err) {
                console.error('MNID capture error:', err);
                alert('Download failed. Check the browser console for details.');
            });
    }

    // ── Modebar injection ────────────────────────────────────────────────────

    function createModebarButton() {
        var btn = document.createElement('a');
        btn.className = 'modebar-btn mnid-capture-modebar-btn';
        btn.setAttribute('rel', 'tooltip');
        btn.setAttribute('data-title', 'Download chart as PNG');
        btn.innerHTML = CAMERA_SVG;
        btn.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            var gd = this.closest('.js-plotly-plot');
            if (gd) captureCardByGd(gd);
        });
        return btn;
    }

    function removePlotlyDownloadBtn(modebar) {
        var btns = modebar.querySelectorAll('.modebar-btn');
        for (var i = 0; i < btns.length; i++) {
            var title = (btns[i].getAttribute('data-title') || '').toLowerCase();
            if (title.indexOf('download') !== -1) {
                btns[i].parentNode.removeChild(btns[i]);
                return;
            }
        }
    }

    function injectModebarButton(modebar) {
        removePlotlyDownloadBtn(modebar);
        if (modebar.querySelector('.mnid-capture-modebar-btn')) return;

        var btn = createModebarButton();
        var groups = modebar.querySelectorAll('.modebar-group');
        if (groups.length > 0) {
            groups[groups.length - 1].appendChild(btn);
        } else {
            modebar.appendChild(btn);
        }
    }

    function scanModebars(root) {
        var modebars = [];
        if (root.classList && root.classList.contains('modebar')) {
            modebars = [root];
        }
        if (root.querySelectorAll) {
            var nested = root.querySelectorAll('.modebar');
            for (var i = 0; i < nested.length; i++) {
                modebars.push(nested[i]);
            }
        }
        for (var j = 0; j < modebars.length; j++) {
            injectModebarButton(modebars[j]);
        }
    }

    function boot() {
        var existing = document.querySelectorAll('.modebar');
        for (var i = 0; i < existing.length; i++) {
            injectModebarButton(existing[i]);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot, { once: true });
    } else {
        boot();
    }

    new MutationObserver(function (mutations) {
        for (var m = 0; m < mutations.length; m++) {
            var added = mutations[m].addedNodes;
            for (var i = 0; i < added.length; i++) {
                if (added[i].nodeType === 1) {
                    scanModebars(added[i]);
                }
            }
        }
    }).observe(document.body, { childList: true, subtree: true });
})();
