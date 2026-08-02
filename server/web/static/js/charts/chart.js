window.Charts = window.Charts || {};

Charts.Chart = function (canvas, options) {
    options = options || {};
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.options = options;

    this.dataFrom = [];
    this.dataTo = [];
    this.progress = 1;
    this.lastNow = performance.now();

    this.hoverX = null;
    this.rafId = 0;
    this.dpr = 1;
    this.size = { width: 0, height: 0 };

    this.area = new Charts.PlotArea(0, 0, options.padding);
    this.scale = new Charts.Scale({ max: options.fixedMax || 1 });

    this.grid = new Charts.Grid({ ticks: 4, format: options.gridFormat });
    this.timeAxis = new Charts.TimeAxis({ count: 3, format: options.timeFormat });
    this.areaFill = new Charts.Area();
    this.line = new Charts.Line();
    this.valueChip = new Charts.ValueChip({ format: options.chipFormat });
    this.crosshair = new Charts.Crosshair();
    this.tooltip = new Charts.Tooltip({
        formatValue: options.tipFormat,
        formatTime: options.timeFormat
    });

    this.bind();
    this.resize();
    this.schedule();
};

Charts.Chart.prototype.setData = function (points, max) {
    this.scale.setMax(max);
    var to = [];
    for (var i = 0; i < points.length; i++) {
        to.push({ t: points[i].t, v: points[i].v });
    }
    if (this.dataTo.length !== to.length) {
        this.dataFrom = to.map(function (p) { return { t: p.t, v: p.v }; });
        this.progress = 1;
    } else {
        this.dataFrom = this.dataTo.slice();
        this.progress = 0;
        this.lastNow = performance.now();
    }
    this.dataTo = to;
    this.schedule();
};

Charts.Chart.prototype.schedule = function () {
    if (this.rafId) return;
    var self = this;
    this.rafId = requestAnimationFrame(function (now) { self.loop(now); });
};

Charts.Chart.prototype.loop = function (now) {
    this.rafId = 0;
    if (this.progress < 1) {
        this.progress = Math.min(1, this.progress + (now - this.lastNow) / (this.options.duration || 300));
        this.lastNow = now;
    }
    this.draw();
    if (this.progress < 1) this.schedule();
};

Charts.Chart.prototype.redraw = function () {
    this.schedule();
};

Charts.Chart.prototype.resize = function () {
    var w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (!w || !h) return;
    this.dpr = Math.max(1, window.devicePixelRatio || 1);
    this.size = { width: w, height: h };
    var bw = Math.round(w * this.dpr), bh = Math.round(h * this.dpr);
    if (this.canvas.width !== bw) this.canvas.width = bw;
    if (this.canvas.height !== bh) this.canvas.height = bh;
    this.area.resize(w, h);
    this.schedule();
};

Charts.Chart.prototype.bind = function () {
    var self = this;
    this._onMove = function (e) {
        var r = self.canvas.getBoundingClientRect();
        self.hoverX = e.clientX - r.left;
        self.schedule();
    };
    this._onLeave = function () {
        self.hoverX = null;
        self.schedule();
    };
    this._onResize = function () {
        self.resize();
        self.schedule();
    };
    this.canvas.addEventListener('pointermove', this._onMove);
    this.canvas.addEventListener('pointerleave', this._onLeave);
    window.addEventListener('resize', this._onResize);
    if (typeof ResizeObserver !== 'undefined') {
        this._observer = new ResizeObserver(function () {
            self.resize();
            self.schedule();
        });
        this._observer.observe(this.canvas.parentNode || this.canvas);
    }
};

Charts.Chart.prototype.dispose = function () {
    if (this._observer) this._observer.disconnect();
    window.removeEventListener('resize', this._onResize);
    this.canvas.removeEventListener('pointermove', this._onMove);
    this.canvas.removeEventListener('pointerleave', this._onLeave);
};

Charts.Chart.prototype.interpolatedPoints = function (ease) {
    var out = [];
    for (var i = 0; i < this.dataTo.length; i++) {
        var f = this.dataFrom[i];
        out.push(f ? {
            t: f.t + (this.dataTo[i].t - f.t) * ease,
            v: f.v + (this.dataTo[i].v - f.v) * ease
        } : { t: this.dataTo[i].t, v: this.dataTo[i].v });
    }
    return out;
};

Charts.Chart.prototype.hoverIndex = function (pts) {
    if (this.hoverX == null) return -1;
    var area = this.area;
    if (this.hoverX < area.x0 || this.hoverX > area.x1) return -1;
    var span = this.scale.t1 - this.scale.t0;
    var t = this.scale.t0 + (this.hoverX - area.x0) / (area.x1 - area.x0) * span;
    var best = -1, bd = Infinity;
    for (var i = 0; i < pts.length; i++) {
        var d = Math.abs(pts[i].t - t);
        if (d < bd) { bd = d; best = i; }
    }
    return best;
};

Charts.Chart.prototype.draw = function () {
    var W = this.size.width, H = this.size.height;
    if (!W || !H) return;
    var ctx = this.ctx;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    var options = this.options;
    var color = Charts.cssVar(options.colorVar) || '#6200EE';
    var theme = {
        color: color,
        hint: Charts.cssVar('--text-hint') || 'rgba(0,0,0,.38)',
        divider: Charts.cssVar('--divider') || 'rgba(0,0,0,.12)'
    };

    this.scale.setArea(this.area);
    var pts = this.interpolatedPoints(Charts.easeInOutCubic(this.progress));
    if (pts.length) {
        this.scale.setTimeRange(pts[0].t, pts[pts.length - 1].t);
    }

    this.grid.draw(ctx, this.scale, theme);
    if (pts.length) this.timeAxis.draw(ctx, this.scale, theme);

    if (pts.length < 2) {
        ctx.fillStyle = theme.hint;
        ctx.font = Charts.uiFont('500', 12);
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('Collecting data...', (this.area.x0 + this.area.x1) / 2, (this.area.y0 + this.area.y1) / 2);
        return;
    }

    var xy = [];
    for (var i = 0; i < pts.length; i++) {
        xy.push([this.scale.x(pts[i].t), this.scale.y(pts[i].v)]);
    }

    var path = options.stepped ? Charts.Path.stepped(xy) : Charts.Path.smooth(xy);
    var closed = Charts.Path.closed(path, xy[0][0], xy[xy.length - 1][0], this.area.y1);

    this.areaFill.draw(ctx, closed, this.scale, color);
    this.line.draw(ctx, path, color);
    this.valueChip.draw(ctx, pts[pts.length - 1].v, this.scale, color);

    var hover = this.hoverIndex(pts);
    if (hover >= 0) {
        var point = { x: xy[hover][0], y: xy[hover][1], value: pts[hover].v, time: pts[hover].t };
        this.crosshair.draw(ctx, point, this.scale, theme, color);
        this.tooltip.draw(ctx, point, this.scale);
    }
};
