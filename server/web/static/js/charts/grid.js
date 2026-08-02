window.Charts = window.Charts || {};

Charts.Grid = function (options) {
    options = options || {};
    this.ticks = options.ticks || 4;
    this.format = options.format || function (v) { return Math.round(v); };
};

Charts.Grid.prototype.draw = function (ctx, scale, theme) {
    var area = scale.area;
    ctx.strokeStyle = theme.divider;
    ctx.lineWidth = 1;
    ctx.font = Charts.monoFont('500', 10);
    ctx.textBaseline = 'middle';
    for (var g = 0; g <= this.ticks; g++) {
        var gy = area.y0 + (area.y1 - area.y0) * (g / this.ticks);
        ctx.beginPath();
        ctx.moveTo(area.x0, Math.round(gy) + 0.5);
        ctx.lineTo(area.x1, Math.round(gy) + 0.5);
        ctx.stroke();
        ctx.fillStyle = theme.hint;
        ctx.textAlign = 'right';
        ctx.fillText(this.format(scale.max * (1 - g / this.ticks)), area.x0 - 8, gy);
    }
};
