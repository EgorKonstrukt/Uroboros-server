window.Charts = window.Charts || {};

Charts.Line = function (options) {
    options = options || {};
    this.glow = options.glow == null ? 5 : options.glow;
    this.width = options.width || 2;
    this.glowOpacity = options.glowOpacity == null ? 0.16 : options.glowOpacity;
};

Charts.Line.prototype.draw = function (ctx, path, color) {
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.strokeStyle = Charts.hexA(color, this.glowOpacity);
    ctx.lineWidth = this.glow;
    ctx.stroke(new Path2D(path));
    ctx.strokeStyle = color;
    ctx.lineWidth = this.width;
    ctx.stroke(new Path2D(path));
};
