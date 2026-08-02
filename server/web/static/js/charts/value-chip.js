window.Charts = window.Charts || {};

Charts.ValueChip = function (options) {
    options = options || {};
    this.format = options.format || function (v) { return Math.round(v); };
};

Charts.ValueChip.prototype.draw = function (ctx, value, scale, color) {
    var area = scale.area;
    ctx.font = Charts.monoFont('600', 21);
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = color;
    ctx.fillText(this.format(value), area.x1, area.y0 - 12);
};
