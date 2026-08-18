/**
 * MNID dashboard per-chart PNG capture & CSV export.
 *
 * Injects custom download buttons into Plotly's modebar (replacing the
 * built-in one) on every chart that has the modebar enabled.
 * - Camera button: PNG capture with branded header (all charts)
 * - Table button: CSV data export (line/run charts only)
 */
(function () {
  "use strict";

  // Layout constants
  var PAD = 14;
  var DIVIDER_Y_GAP = 10;
  var HEADER_WIDTH = 640;

  // Feather camera icon
  var CAMERA_SVG =
    '<svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:block;margin:0 auto;">' +
    '<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>' +
    '<circle cx="12" cy="13" r="4"/>' +
    "</svg>";

  // Feather download icon for CSV
  var TABLE_SVG =
    '<svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:block;margin:0 auto;">' +
    '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>' +
    '<polyline points="7 10 12 15 17 10"/>' +
    '<line x1="12" y1="15" x2="12" y2="3"/>' +
    "</svg>";

  // Data helpers

  function stripHtml(text) {
    // Plotly figure titles can carry HTML (<b>, <span style="..."> etc.) for
    // in-plot styling -- this canvas/filename code draws plain text, so any
    // tag that leaks through here shows up literally (e.g. "<b>Total
    // Births</b>") instead of being rendered.
    return (text || "").replace(/<[^>]*>/g, "").trim();
  }

  function readStoreData() {
    var storeEl = document.getElementById("mnid-capture-store");
    if (!storeEl) return null;
    try {
      var raw = storeEl.getAttribute("data-capture");
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (e) {
      return null;
    }
  }

  function findCategoryInfo(card) {
    var pillsRow = card.querySelector(".mnid-pills");
    if (!pillsRow) return { title: fallbackTitle(card), pills: [] };

    var titleEl = pillsRow.previousElementSibling;
    var title = titleEl ? titleEl.textContent.trim() : "";

    var pills = [];
    var pillEls = pillsRow.querySelectorAll(".mnid-pill");
    for (var i = 0; i < pillEls.length; i++) {
      pills.push({
        text: pillEls[i].textContent.trim(),
        cls: pillEls[i].className,
      });
    }

    if (!title) title = fallbackTitle(card);

    return { title: title, pills: pills };
  }

  function fallbackTitle(card) {
    var cardTitle = card.querySelector(".mnid-card-title");
    if (cardTitle) return cardTitle.textContent.trim();

    // `card` is sometimes the .js-plotly-plot div itself (callers that found
    // no .mnid-chart-card ancestor pass `gd` straight through) -- querySelector
    // only matches descendants, never the element itself, so that case used to
    // silently find nothing here and fall through to the literal "Chart"
    // below even when the figure has a real title baked into its own layout.
    var gd =
      card.classList && card.classList.contains("js-plotly-plot")
        ? card
        : card.querySelector(".js-plotly-plot");
    if (
      gd &&
      gd._fullLayout &&
      gd._fullLayout.title &&
      gd._fullLayout.title.text
    ) {
      var t = stripHtml(gd._fullLayout.title.text);
      if (t && t !== "Click to enter Plot title") return t;
    }

    var section = card.closest("section, [id]");
    if (section) {
      var sectionLbl = section.querySelector(".mnid-section-lbl");
      if (sectionLbl) return sectionLbl.textContent.trim();
    }

    return "Chart";
  }

  // Canvas drawing

  function textWidth(ctx, text, font) {
    ctx.save();
    ctx.font = font;
    var w = ctx.measureText(text).width;
    ctx.restore();
    return w;
  }

  function drawHeader(ctx, w, data, catInfo) {
    // Calculate total header height first
    var h = PAD + 22; // title
    if (catInfo.pills.length > 0) h += 18; // pills
    h += 6; // gap
    h += DIVIDER_Y_GAP; // divider space
    h += 18 * 2; // filter rows
    h += 8; // bottom padding

    // White card background
    ctx.fillStyle = "#ffffff";
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

    ctx.fillStyle = "#0F172A";
    ctx.font =
      'bold 17px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    ctx.textBaseline = "top";
    ctx.textAlign = "left";
    ctx.fillText(catInfo.title, PAD, pos);
    pos += 22;

    var pills = catInfo.pills;
    if (pills.length > 0) {
      var pillX = PAD;
      for (var i = 0; i < pills.length; i++) {
        var fg = "#475569";
        for (var k in PILL_COLORS) {
          if (pills[i].cls.indexOf(k) !== -1) {
            fg = PILL_COLORS[k].fg;
            break;
          }
        }
        ctx.font =
          'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        ctx.fillStyle = fg;
        ctx.fillText(pills[i].text, pillX, pos + 4);
        pillX += textWidth(ctx, pills[i].text, ctx.font) + 14;
      }
      pos += 18;
    }

    pos += 6;

    // Divider line
    ctx.strokeStyle = "#E2E8F0";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(PAD, pos);
    ctx.lineTo(w - PAD, pos);
    ctx.stroke();

    pos += DIVIDER_Y_GAP;

    // Filter context — two-column layout
    ctx.textAlign = "left";
    var metaFont =
      '11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    var metaFontBold =
      'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    var labelColor = "#94A3B8";
    var valueColor = "#334155";
    var colW = 220;
    var lineH = 18;

    [
      // Row 1: Facility | District
      [
        { label: "Facility", value: data.facility || "N/A" },
        { label: "District", value: data.district || "N/A" },
      ],
      // Row 2: Period | Program
      [
        { label: "Period", value: data.period || "N/A" },
        { label: "Program", value: data.program || "N/A" },
      ],
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
    "mnid-pill-green": { fg: "#16A34A" },
    "mnid-pill-amber": { fg: "#7A5A00" },
    "mnid-pill-red": { fg: "#B91C1C" },
    "mnid-pill-blue": { fg: "#475569" },
  };

  // CSV export

  function isLineChart(gd) {
    if (!gd || !gd.data) return false;
    for (var i = 0; i < gd.data.length; i++) {
      var type = (gd.data[i].type || "").toLowerCase();
      if (type === "scatter" || type === "scattergl" || type === "bar") return true;
    }
    return false;
  }

  function parseMeasureLabel(trace) {
    var ht = trace.hovertemplate;
    if (!ht) return null;
    if (Array.isArray(ht)) ht = ht[0];
    var match = String(ht).match(/(Median|Moving\s*avg):/i);
    if (!match) return null;
    var raw = match[1];
    if (raw.toLowerCase() === "moving avg") return "Moving Avg";
    return raw.charAt(0).toUpperCase() + raw.slice(1).toLowerCase();
  }

  function parseCustomdataLayout(trace) {
    var ht = trace.hovertemplate;
    if (!ht) return null;
    if (Array.isArray(ht)) ht = ht[0];
    var s = String(ht);

    // "Actual: <b>%{customdata[N]...}" → index N, or no index (flat)
    var actualMatch = s.match(/Actual:\s*[^%]*%\{customdata(?:\[(\d+)\])?/i);
    var actualIndex = actualMatch
      ? (actualMatch[1] !== undefined ? parseInt(actualMatch[1], 10) : null)
      : null;

    // "Clients: %{customdata[M]...} / %{customdata[K]...}"
    var clientsMatch = s.match(/Clients:\s*[^%]*%\{customdata\[(\d+)\][\s\S]*?\/\s*[^%]*%\{customdata\[(\d+)\]/i);
    var numIndex = clientsMatch ? parseInt(clientsMatch[1], 10) : null;
    var denIndex = clientsMatch ? parseInt(clientsMatch[2], 10) : null;

    return { actualIndex: actualIndex, numIndex: numIndex, denIndex: denIndex };
  }

  function extractActualValues(trace) {
    if (!trace.customdata || !trace.customdata.length) return null;
    if (!trace.y || !trace.y.length) return null;
    if (trace.y.length !== trace.customdata.length) return null;

    var isTuple = Array.isArray(trace.customdata[0]);
    if (
      !isTuple &&
      trace.customdata[0] !== null &&
      typeof trace.customdata[0] === "object"
    )
      return null;

    var layout = parseCustomdataLayout(trace);
    var actualIdx = layout && layout.actualIndex !== null ? layout.actualIndex : (isTuple ? 1 : null);
    var numIdx = layout ? layout.numIndex : null;
    var denIdx = layout ? layout.denIndex : null;
    var hasClients = isTuple && numIdx !== null && denIdx !== null;

    var actuals = [];
    var clientsNum = hasClients ? [] : null;
    var clientsDen = hasClients ? [] : null;

    for (var i = 0; i < trace.y.length; i++) {
      var cd = trace.customdata[i];
      var actual;
      if (isTuple) {
        if (actualIdx !== null && actualIdx < cd.length) {
          actual = cd[actualIdx];
        } else {
          actual = cd.length >= 2 ? cd[1] : cd[0];
        }
        if (hasClients) {
          clientsNum.push(
            numIdx < cd.length && cd[numIdx] !== undefined && cd[numIdx] !== null ? Number(cd[numIdx]) : undefined,
          );
          clientsDen.push(
            denIdx < cd.length && cd[denIdx] !== undefined && cd[denIdx] !== null ? Number(cd[denIdx]) : undefined,
          );
        }
      } else {
        actual = cd;
      }
      if (actual === undefined || actual === null) {
        actuals.push(undefined);
        continue;
      }
      var num = Number(actual);
      actuals.push(isNaN(num) ? actual : num);
    }

    return {
      actuals: actuals,
      clientsNum: clientsNum,
      clientsDen: clientsDen,
    };
  }

  function exportCSV(gd, ctx) {
    var traces = gd.data;
    if (!traces || !traces.length) {
      alert("No data available for CSV export.");
      return;
    }

    var clean = function (v) {
      return String(v)
        .replace(/<[^>]+>/g, " ")
        .replace(/\s+/g, " ")
        .trim();
    };
    var esc = function (v) {
      return '"' + String(v).replace(/"/g, '""') + '"';
    };

    var rows = [];
    var title = (ctx.chartTitle || "")
      .replace(/\s*Target\s+\d+%\s*$/i, "")
      .replace(/\s*\d+\s*available/gi, "")
      .replace(/\s*\d+\s*awaiting/gi, "")
      .replace(/\s*Avg\s*\d+%/gi, "")
      .trim();

    // Metadata header
    rows.push(esc("Facility") + "," + esc(clean(ctx.facility || "N/A")));
    rows.push(esc("District") + "," + esc(clean(ctx.district || "N/A")));
    rows.push(esc("Period") + "," + esc(clean(ctx.period || "N/A")));
    rows.push(esc("Program") + "," + esc(clean(ctx.program || "N/A")));
    rows.push("");

    // Precompute run-chart metadata for each trace
    var runData = [];
    var isRunChart = [];
    for (var i = 0; i < traces.length; i++) {
      runData[i] = extractActualValues(traces[i]);
      isRunChart[i] = runData[i] !== null;
    }
    var hasRunTraces = false;
    for (var k = 0; k < isRunChart.length; k++) {
      if (isRunChart[k]) { hasRunTraces = true; break; }
    }

    // Detect percentage axis
    var isPct = /%|percent/i.test(
      (gd._fullLayout && gd._fullLayout.yaxis && gd._fullLayout.yaxis.title)
        ? (gd._fullLayout.yaxis.title.text || "")
        : ""
    );

    // Detect bar charts
    var isBar = traces[0] && traces[0].type === "bar" && !hasRunTraces;

    // Bar chart: fallback percentage detection from x-values
    if (isBar && !isPct && traces[0].x && traces[0].x.length) {
      var looksPct = true;
      var checkLen = Math.min(traces[0].x.length, 10);
      for (var px = 0; px < checkLen; px++) {
        if (Number(traces[0].x[px]) > 120) { looksPct = false; break; }
      }
      if (looksPct) isPct = true;
    }

    // Bar chart: indicator-per-row layout
    if (isBar) {
      var barHeaders = [esc("Indicator"), esc(clean(title || "Value"))];
      rows.push(barHeaders.join(","));

      var barTrace = traces[0];
      if (barTrace.y && barTrace.x) {
        for (var br = 0; br < barTrace.y.length; br++) {
          var brow = [esc(clean(barTrace.y[br]))];
          var bval = barTrace.x[br];
          var bdisp = bval !== undefined && bval !== null ? clean(bval) : "";
          if (isPct && bdisp) bdisp += "%";
          brow.push(bdisp);
          rows.push(brow.join(","));
        }
      }
    } else {
      // Line / run chart layout

      // Column headers
      var xTrace = traces[0];
      var xTitle = isRunChart[0] ? "Date" : (xTrace.name || "Date");
      var headers = [clean(xTitle)];
      for (var i = 0; i < traces.length; i++) {
        if (i === 0 && xTrace.x && !isRunChart[i]) continue;
        var rawName = clean(traces[i].name || "");
        if (!isRunChart[i] && hasRunTraces && (/^Indicator\s+\d+$/.test(rawName) || rawName.toLowerCase() === "trace" || !rawName || rawName.toLowerCase().indexOf("target") !== -1)) continue;
        var name;
        if (isRunChart[i]) {
          name = clean(traces[i].name || "");
          if (/^Indicator\s+\d+$/.test(name) || name.toLowerCase() === "trace" || !name) {
            name = clean(title || "");
          }
        } else {
          name = rawName;
          if (/^Indicator\s+\d+$/.test(name) || name.toLowerCase() === "trace") {
            name = clean(title || "");
          }
        }
        if (!name) name = "Indicator " + (i + 1);
        if (isRunChart[i]) {
          var measureLabel = parseMeasureLabel(traces[i]);
          headers.push(name + " (" + (measureLabel || "Value") + ")");
          headers.push(name + " (Actual)");
          if (runData[i].clientsNum !== null) {
            headers.push(name + " (Clients)");
          }
        } else {
          headers.push(name);
        }
      }
      rows.push(headers.map(esc).join(","));

      // Data rows — align by date (x value), not index, so multi-series
      // charts whose traces have differing/missing periods stay aligned.
      var dateOrder = [];
      var dateSeen = {};
      for (var t = 0; t < traces.length; t++) {
        if (traces[t].x) {
          for (var xi = 0; xi < traces[t].x.length; xi++) {
            var dv = traces[t].x[xi];
            if (dateSeen[dv] === undefined) {
              dateSeen[dv] = true;
              dateOrder.push(dv);
            }
          }
        }
      }
      dateOrder.sort(function (a, b) {
        var da = new Date(a);
        var db = new Date(b);
        if (!isNaN(da) && !isNaN(db)) return da - db;
        return a < b ? -1 : (a > b ? 1 : 0);
      });

      var traceDateIndex = [];
      for (var t = 0; t < traces.length; t++) {
        var map = {};
        if (traces[t].x) {
          for (var xi = 0; xi < traces[t].x.length; xi++) {
            map[traces[t].x[xi]] = xi;
          }
        }
        traceDateIndex.push(map);
      }

      for (var dr = 0; dr < dateOrder.length; dr++) {
        var row = [esc(clean(dateOrder[dr]))];
        for (var j = 0; j < traces.length; j++) {
          if (j === 0 && xTrace.x && !isRunChart[j]) continue;
          var rawNameJ = clean(traces[j].name || "");
          if (!isRunChart[j] && hasRunTraces && (/^Indicator\s+\d+$/.test(rawNameJ) || rawNameJ.toLowerCase() === "trace" || !rawNameJ || rawNameJ.toLowerCase().indexOf("target") !== -1)) continue;
          var idx = traceDateIndex[j][dateOrder[dr]];
          if (isRunChart[j]) {
            var yVal = idx !== undefined && traces[j].y ? traces[j].y[idx] : undefined;
            row.push(yVal !== undefined && yVal !== null ? clean(yVal) : "");
            var actVal = idx !== undefined && runData[j].actuals ? runData[j].actuals[idx] : undefined;
            var actDisp = actVal !== undefined && actVal !== null ? clean(actVal) : "";
            if (isPct && actDisp) actDisp += "%";
            row.push(actDisp);
            if (runData[j].clientsNum !== null) {
              var numVal = idx !== undefined ? runData[j].clientsNum[idx] : undefined;
              var denVal = idx !== undefined ? runData[j].clientsDen[idx] : undefined;
              if (numVal !== undefined && numVal !== null && denVal !== undefined && denVal !== null && !isNaN(numVal) && !isNaN(denVal)) {
                row.push(clean(numVal) + "/" + clean(denVal));
              } else {
                row.push(actDisp);
              }
            }
          } else {
            var yVal2 = idx !== undefined && traces[j].y ? traces[j].y[idx] : undefined;
            row.push(yVal2 !== undefined && yVal2 !== null ? clean(yVal2) : "");
          }
        }
        rows.push(row.join(","));
      }
    }

    var csv = rows.join("\n");
    var blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    var safeTitle = (title || "chart")
      .replace(/[^a-zA-Z0-9_\- ]/g, "")
      .replace(/\s+/g, "_");
    var safeFacility = (ctx.facility || "report")
      .replace(/[^a-zA-Z0-9_\- ]/g, "")
      .replace(/\s+/g, "_");
    link.download = safeTitle + "_" + safeFacility + ".csv";
    link.href = url;
    link.click();
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 100);
  }

  // Capture & download

  function captureCardByGd(gd) {
    var data = readStoreData();
    if (!data) {
      alert(
        "Cannot download: dashboard filter information not available on this page.",
      );
      return;
    }

    var card = gd.closest(".mnid-chart-card");
    var catInfo = card
      ? findCategoryInfo(card)
      : { title: fallbackTitle(gd), pills: [] };
    // getIndicatorName checks the figure's own trace names / baked-in title
    // first -- more reliable than the DOM-scraping fallback chain above for
    // cards (like run charts) that don't wrap in .mnid-chart-card at all.
    var resolvedIndicatorName = getIndicatorName(gd);
    if (resolvedIndicatorName && (!catInfo.title || catInfo.title === "Chart")) {
      catInfo.title = resolvedIndicatorName;
    }

    // Use the actual chart DOM size to preserve aspect ratio
    var domW = gd.offsetWidth || 540;
    var domH = gd.offsetHeight || 300;
    var ratio = domH / domW;

    var plotW = HEADER_WIDTH * 2;
    var plotH = Math.round(plotW * ratio);

    Plotly.toImage(gd, { format: "png", width: plotW, height: plotH, scale: 1 })
      .then(function (chartDataUrl) {
        var chartImg = new Image();
        chartImg.onload = function () {
          // Measure exact header height by drawing to a scratch canvas
          var scratch = document.createElement("canvas");
          scratch.width = plotW;
          scratch.height = 2000;
          var sctx = scratch.getContext("2d");
          var hdrH = drawHeader(sctx, plotW, data, catInfo);

          var chartH = Math.round(
            chartImg.naturalHeight * (plotW / chartImg.naturalWidth),
          );
          var totalH = hdrH + chartH;

          var canvas = document.createElement("canvas");
          canvas.width = plotW;
          canvas.height = totalH;
          var ctx = canvas.getContext("2d");

          ctx.fillStyle = "#F8FAFC";
          ctx.fillRect(0, 0, plotW, totalH);

          // Draw header + chart from scratch image
          ctx.drawImage(scratch, 0, 0);
          ctx.drawImage(chartImg, 0, hdrH, plotW, chartH);

          canvas.toBlob(function (blob) {
            var safeTitle = (catInfo.title || "chart")
              .replace(/[^a-zA-Z0-9_\- ]/g, "")
              .replace(/\s+/g, "_");
            var safeFacility = (data.facility || "report")
              .replace(/[^a-zA-Z0-9_\- ]/g, "")
              .replace(/\s+/g, "_");
            var url = URL.createObjectURL(blob);
            var link = document.createElement("a");
            link.download = safeTitle + "_" + safeFacility + ".png";
            link.href = url;
            link.click();
            setTimeout(function () {
              URL.revokeObjectURL(url);
            }, 100);
          }, "image/png");
        };
        chartImg.onerror = function () {
          alert("Failed to load chart image for download.");
        };
        chartImg.src = chartDataUrl;
      })
      .catch(function (err) {
        console.error("MNID capture error:", err);
        alert("Download failed. Check the browser console for details.");
      });
  }

  // Modebar injection

  function createModebarButton() {
    var btn = document.createElement("a");
    btn.className = "modebar-btn mnid-capture-modebar-btn";
    btn.setAttribute("rel", "tooltip");
    btn.setAttribute("data-title", "Download chart as PNG");
    btn.innerHTML = CAMERA_SVG;
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      var gd = this.closest(".js-plotly-plot");
      if (gd) captureCardByGd(gd);
    });
    return btn;
  }

  function removePlotlyDownloadBtn(modebar) {
    var btns = modebar.querySelectorAll(".modebar-btn");
    for (var i = 0; i < btns.length; i++) {
      var title = (btns[i].getAttribute("data-title") || "").toLowerCase();
      var isOurs =
        btns[i].classList.contains("mnid-capture-png-btn") ||
        btns[i].classList.contains("mnid-capture-csv-btn");
      if (!isOurs && title.indexOf("download") !== -1) {
        btns[i].parentNode.removeChild(btns[i]);
        return;
      }
    }
  }

  function getIndicatorName(gd) {
    // 1. Trace names (skip auto-generated)
    if (gd && gd.data) {
      for (var i = 0; i < gd.data.length; i++) {
        var n = gd.data[i].name;
        if (n && !/^Indicator\s+\d+$/.test(n) && n.toLowerCase() !== "trace" && n.toLowerCase() !== "target")
          return n;
      }
    }
    // 2. Plotly figure title
    if (
      gd &&
      gd._fullLayout &&
      gd._fullLayout.title &&
      gd._fullLayout.title.text
    ) {
      var t = stripHtml(gd._fullLayout.title.text);
      if (t && t !== "Click to enter Plot title") return t;
    }
    // 3. DOM: first text child of the chart card (run chart indicator label)
    var card = gd && gd.closest(".mnid-chart-card");
    if (card) {
      var children = card.children;
      for (var j = 0; j < children.length; j++) {
        var child = children[j];
        if (child.classList.contains("js-plotly-plot")) continue;
        if (child.classList.contains("modebar")) continue;
        if (child.querySelector && child.querySelector(".js-plotly-plot"))
          continue;
        var text = (child.textContent || "").trim();
        if (text && text.length < 120 && !/^Target/.test(text)) {
          return text.split(/[\n\r]/)[0].trim();
        }
      }
    }
    return null;
  }

  function createCSVButton() {
    var btn = document.createElement("a");
    btn.className = "modebar-btn mnid-capture-modebar-btn";
    btn.setAttribute("rel", "tooltip");
    btn.setAttribute("data-title", "Download data as CSV");
    btn.innerHTML = TABLE_SVG;
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      var gd = this.closest(".js-plotly-plot");
      if (!gd) return;
      var data = readStoreData();
      var indicatorName = getIndicatorName(gd);
      var card = gd.closest(".mnid-chart-card");
      var catInfo = card
        ? findCategoryInfo(card)
        : { title: fallbackTitle(gd), pills: [] };
      var chartTitle = indicatorName || catInfo.title || "Chart";
      exportCSV(gd, {
        chartTitle: chartTitle,
        facility: (data && data.facility) || "N/A",
        district: (data && data.district) || "N/A",
        period: (data && data.period) || "N/A",
        program: (data && data.program) || "N/A",
      });
    });
    return btn;
  }

  function injectModebarButton(modebar) {
    removePlotlyDownloadBtn(modebar);

    var groups = modebar.querySelectorAll(".modebar-group");
    var lastGroup = groups.length > 0 ? groups[groups.length - 1] : modebar;

    // PNG button
    if (!modebar.querySelector(".mnid-capture-png-btn")) {
      var pngBtn = createModebarButton();
      pngBtn.classList.add("mnid-capture-png-btn");
      lastGroup.appendChild(pngBtn);
    }

    // CSV button — only for line/run charts
    var gd = modebar.closest(".js-plotly-plot");
    if (gd && isLineChart(gd) && !gd.closest(".mnid-heatmap-map")) {
      if (!modebar.querySelector(".mnid-capture-csv-btn")) {
        var csvBtn = createCSVButton();
        csvBtn.classList.add("mnid-capture-csv-btn");
        lastGroup.appendChild(csvBtn);
      }
    }
  }

  function scanModebars(root) {
    var modebars = [];
    if (root.classList && root.classList.contains("modebar")) {
      modebars = [root];
    }
    if (root.querySelectorAll) {
      var nested = root.querySelectorAll(".modebar");
      for (var i = 0; i < nested.length; i++) {
        modebars.push(nested[i]);
      }
    }
    for (var j = 0; j < modebars.length; j++) {
      injectModebarButton(modebars[j]);
    }
  }

  function boot() {
    var existing = document.querySelectorAll(".modebar");
    for (var i = 0; i < existing.length; i++) {
      injectModebarButton(existing[i]);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
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

  // Re-scan periodically so the CSV button is injected once a chart's data
  // loads via callback after the initial empty render (e.g. comparison charts).
  // `boot()` is idempotent; this avoids relying on Plotly's DOM events, which
  // are not dispatched as bubbling events in the bundled plotly.min.js.
  setInterval(function () {
    boot();
  }, 1000);
})();
