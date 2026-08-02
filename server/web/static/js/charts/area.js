window.Charts = window.Charts || {};

Charts.Area = function (options) {
    options = options || {};
    this.opacity = options.opacity || 0.22;
};

Charts.Area.prototype.draw = function (ctx, path, scale, color) {
    var area = scale.area;
    var grad = ctx.createLinearGradient(0, area.y0, 0, area.y1);
    grad.addColorStop(0, Charts.hexA(color, 0));
    grad.addColorStop(1, Charts.hexA(color, this.opacity));
    ctx.fillStyle = grad;
    ctx.fill(new Path2D(path));
};
