window.Charts = window.Charts || {};

Charts.Tooltip = function (options) {
    options = options || {};
    this.formatValue = options.formatValue || function (v) { return String(Math.round(v)); };
    this.formatTime = options.formatTime || Charts.clock;
    this.height = options.height || 26;
};

Charts.Tooltip.prototype.draw = function (ctx, point, scale) {
    var area = scale.area;
    var text = this.formatValue(point.value) + ' \u00b7 ' + this.formatTime(point.time);
    ctx.font = Charts.monoFont('600', 11);
    var tw = ctx.measureText(text).width;
    var bx = point.x + 12;
    if (bx + tw + 16 > area.x1) bx = point.x - tw - 28;
    if (bx < area.x0) bx = area.x0;
    var by = Math.max(area.y0 + 2, point.y - 46);
    Charts.roundRectPath(ctx, bx, by, tw + 16, this.height, 6);
    ctx.fillStyle = 'rgba(23,23,23,.92)';
    ctx.fill();
    ctx.fillStyle = '#ffffff';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, bx + 8, by + 13);
};
