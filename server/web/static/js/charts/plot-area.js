window.Charts = window.Charts || {};

Charts.PlotArea = function (width, height, padding) {
    this.padding = padding || {};
    this.resize(width, height);
};

Charts.PlotArea.prototype.resize = function (width, height) {
    var p = this.padding;
    this.width = width;
    this.height = height;
    this.x0 = p.left || 46;
    this.x1 = width - (p.right || 14);
    this.y0 = p.top || 34;
    this.y1 = height - (p.bottom || 22);
};
