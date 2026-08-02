window.Charts = window.Charts || {};

Charts.TimeAxis = function (options) {
    options = options || {};
    this.count = options.count || 3;
    this.format = options.format || Charts.clock;
};

Charts.TimeAxis.prototype.draw = function (ctx, scale, theme) {
    var area = scale.area;
    var count = Math.max(2, this.count);
    ctx.font = Charts.monoFont('500', 10);
    ctx.fillStyle = theme.hint;
    ctx.textBaseline = 'top';
    for (var i = 0; i < count; i++) {
        var t = scale.t0 + (scale.t1 - scale.t0) * (i / (count - 1));
        var x = scale.x(t);
        ctx.textAlign = i === 0 ? 'left' : (i === count - 1 ? 'right' : 'center');
        ctx.fillText(this.format(t), x, area.y1 + 7);
    }
};
