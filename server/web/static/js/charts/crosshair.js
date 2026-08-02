window.Charts = window.Charts || {};

Charts.Crosshair = function (options) {
    options = options || {};
    this.dotRadius = options.dotRadius || 4.5;
};

Charts.Crosshair.prototype.draw = function (ctx, point, scale, theme, color) {
    var area = scale.area;
    ctx.strokeStyle = Charts.hexA(theme.hint, 0.6);
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(point.x, area.y0);
    ctx.lineTo(point.x, area.y1);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.beginPath();
    ctx.arc(point.x, point.y, this.dotRadius, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = 'rgba(255,255,255,.95)';
    ctx.stroke();
};
