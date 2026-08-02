window.Charts = window.Charts || {};

Charts.Scale = function (options) {
    options = options || {};
    this.max = options.max || 1;
    this.t0 = 0;
    this.t1 = 1;
    this.area = null;
};

Charts.Scale.prototype.setMax = function (max) {
    this.max = max > 0 ? max : 1;
    return this;
};

Charts.Scale.prototype.setTimeRange = function (t0, t1) {
    this.t0 = t0;
    this.t1 = t1 > t0 ? t1 : t0 + 1;
    return this;
};

Charts.Scale.prototype.setArea = function (area) {
    this.area = area;
    return this;
};

Charts.Scale.prototype.x = function (t) {
    var area = this.area;
    var span = this.t1 - this.t0;
    var x = area.x0 + (t - this.t0) / span * (area.x1 - area.x0);
    return Charts.clamp(x, area.x0, area.x1);
};

Charts.Scale.prototype.y = function (v) {
    var area = this.area;
    var y = area.y1 - (v / this.max) * (area.y1 - area.y0);
    return Charts.clamp(y, area.y0, area.y1);
};
