from distutils.version import LooseVersion
from numbers import Number
import numpy as np
import scipy as sp
from scipy import stats


class KDE:
    """Univariate and bivariate kernel density estimator."""
    def __init__(
        self, *,
        bw_method=None,
        bw_adjust=1,
        gridsize=200,
        cut=3,
        clip=None,
        cumulative=False,
    ):
        """Initialize the estimator with its parameters.

        Parameters
        ----------
        bw_method : string, scalar, or callable, optional
            Method for determining the smoothing bandwidth to use; passed to
            :class:`scipy.stats.gaussian_kde`.
        bw_adjust : number, optional
            Factor that multiplicatively scales the value chosen using
            ``bw_method``. Increasing will make the curve smoother. See Notes.
        gridsize : int, optional
            Number of points on each dimension of the evaluation grid.
        cut : number, optional
            Factor, multiplied by the smoothing bandwidth, that determines how
            far the evaluation grid extends past the extreme datapoints. When
            set to 0, truncate the curve at the data limits.
        clip : pair of numbers None, or a pair of such pairs
            Do not evaluate the density outside of these limits.
        cumulative : bool, optional
            If True, estimate a cumulative distribution function.

        """
        if clip is None:
            clip = None, None

        self.bw_method = bw_method
        self.bw_adjust = bw_adjust
        self.gridsize = gridsize
        self.cut = cut
        self.clip = clip
        self.cumulative = cumulative

        self.support = None

    def _define_support_grid(self, x, bw, cut, clip, gridsize):
        """Create the grid of evaluation points depending for vector x."""
        clip_lo = -np.inf if clip[0] is None else clip[0]
        clip_hi = +np.inf if clip[1] is None else clip[1]
        gridmin = max(x.min() - bw * cut, clip_lo)
        gridmax = min(x.max() + bw * cut, clip_hi)
        return np.linspace(gridmin, gridmax, gridsize)

    def _define_support_univariate(self, x, weights):
        """Create a 1D grid of evaluation points."""
        kde = self._fit(x, weights)
        bw = np.sqrt(kde.covariance.squeeze())
        grid = self._define_support_grid(
            x, bw, self.cut, self.clip, self.gridsize
        )
        return grid

    def _define_support_bivariate(self, x1, x2, weights):
        """Create a 2D grid of evaluation points."""
        clip = self.clip
        if clip[0] is None or np.isscalar(clip[0]):
            clip = (clip, clip)

        kde = self._fit([x1, x2], weights)
        bw = np.sqrt(np.diag(kde.covariance).squeeze())

        grid1 = self._define_support_grid(
            x1, bw[0], self.cut, clip[0], self.gridsize
        )
        grid2 = self._define_support_grid(
            x2, bw[1], self.cut, clip[1], self.gridsize
        )

        return grid1, grid2

    def define_support(self, x1, x2=None, weights=None, cache=True):
        """Create the evaluation grid for a given data set."""
        if x2 is None:
            support = self._define_support_univariate(x1, weights)
        else:
            support = self._define_support_bivariate(x1, x2, weights)

        if cache:
            self.support = support

        return support

    def _fit(self, fit_data, weights=None):
        """Fit the scipy kde while adding bw_adjust logic and version check."""
        fit_kws = {"bw_method": self.bw_method}
        if weights is not None:
            if LooseVersion(sp.__version__) < "1.2.0":
                msg = "Weighted KDE requires scipy >= 1.2.0"
                raise RuntimeError(msg)
            fit_kws["weights"] = weights

        kde = stats.gaussian_kde(fit_data, **fit_kws)
        kde.set_bandwidth(kde.factor * self.bw_adjust)

        return kde

    def _eval_univariate(self, x, weights=None):
        """Fit and evaluate a univariate on univariate data."""
        support = self.support
        if support is None:
            support = self.define_support(x, cache=False)

        kde = self._fit(x, weights)

        if self.cumulative:
            s_0 = support[0]
            density = np.array([
                kde.integrate_box_1d(s_0, s_i) for s_i in support
            ])
        else:
            density = kde(support)

        return density, support

    def _eval_bivariate(self, x1, x2, weights=None):
        """Fit and evaluate a univariate on bivariate data."""
        support = self.support
        if support is None:
            support = self.define_support(x1, x2, cache=False)

        kde = self._fit([x1, x2], weights)

        if self.cumulative:

            grid1, grid2 = support
            density = np.zeros((grid1.size, grid2.size))
            p0 = grid1.min(), grid2.min()
            for i, xi in enumerate(grid1):
                for j, xj in enumerate(grid2):
                    density[i, j] = kde.integrate_box(p0, (xi, xj))

        else:

            xx1, xx2 = np.meshgrid(*support)
            density = kde([xx1.ravel(), xx2.ravel()]).reshape(xx1.shape)

        return density, support

    def __call__(self, x1, x2=None, weights=None):
        """Fit and evaluate on univariate or bivariate data."""
        if x2 is None:
            return self._eval_univariate(x1, weights)
        else:
            return self._eval_bivariate(x1, x2, weights)


class Histogram:
    """Univariate and bivariate histogram estimator."""
    def __init__(
        self,
        stat="count",
        bins="auto",
        binwidth=None,
        binrange=None,
        discrete=False,
        cumulative=False,
    ):
        """Initialize the estimator with its parameters.

        Parameters
        ----------
        stat : {{"count", "density", "probability"}}
            Aggregate statistic to compute in each bin.
        bins : str, number, vector, or a pair of such values
            Passed to :func:`numpy.histogram_bin_edges`.
        binwidth : number or pair of numbers
            Width of each bin, overrides ``bins`` but can be used with
            ``binrange``.
        binrange : pair of numbers or a pair of pairs
            Lowest and highest value for bin edges; can be used either
            with ``bins`` or ``binwidth``.
        discrete : bool
            If True, set ``binwidth`` and ``binrange`` such that bin
            edges cover integer values in the dataset.
        cumulative : bool
            If True, return the cumulative statistic.

        """
        self.stat = stat
        self.bins = bins
        self.binwidth = binwidth
        self.binrange = binrange
        self.discrete = discrete
        self.cumulative = cumulative

        self.bin_edges = None

    def _define_bin_edges(self, x, weights, bins, binwidth, binrange):
        """Inner function that takes bin parameters as arguments."""
        if binrange is None:
            start, stop = x.min(), x.max()
        else:
            start, stop = binrange

        if self.discrete:
            bin_edges = np.arange(start, stop + 2)
        elif binwidth is not None:
            step = binwidth
            bin_edges = np.arange(start, stop + step, step)
        else:
            bin_edges = np.histogram_bin_edges(
                x, bins, binrange, weights,
            )
        return bin_edges

    def define_bin_edges(self, x1, x2=None, weights=None, cache=True):
        """Given data, return the edges of the histogram bins."""
        if x2 is None:

            bin_edges = self._define_bin_edges(
                x1, weights, self.bins, self.binwidth, self.binrange
            )
        else:

            bin_edges = []
            for i, x in enumerate([x1, x2]):

                # Resolve out whether bin parameters are shared
                # or specific to each variable

                bins = self.bins
                if bins is None or isinstance(bins, (str, Number)):
                    pass
                elif isinstance(bins[i], str):
                    bins = bins[i]
                elif len(bins) == 2:
                    bins = bins[i]

                binwidth = self.binwidth
                if binwidth is None:
                    pass
                elif not isinstance(binwidth, Number):
                    binwidth = binwidth[i]

                binrange = self.binrange
                if binrange is None:
                    pass
                elif not isinstance(binrange[0], Number):
                    binrange = binrange[i]

                # Define the bins for this variable

                bin_edges.append(self._define_bin_edges(
                    x, weights, bins, binwidth, binrange,
                ))

            bin_edges = tuple(bin_edges)

        if cache:
            self.bin_edges = bin_edges

        return bin_edges

    def _eval_bivariate(self, x1, x2, weights):
        """Inner function for histogram of two variables."""
        bin_edges = self.bin_edges
        if bin_edges is None:
            bin_edges = self.define_bin_edges(x1, x2, cache=False)

        density = self.stat == "density"

        hist, _, _ = np.histogram2d(
            x1, x2, bin_edges, weights=weights, density=density
        )

        if self.stat == "probability":
            hist = hist.astype(float) / hist.sum()

        if self.cumulative:
            if density:
                area = np.outer(
                    np.diff(bin_edges[0]),
                    np.diff(bin_edges[1]),
                )
                hist = (hist * area).cumsum(axis=0).cumsum(axis=1)
            else:
                hist = hist.cumsum(axis=0).cumsum(axis=1)

        return hist, bin_edges

    def _eval_univariate(self, x, weights):
        """Inner function for histogram of one variable."""
        bin_edges = self.bin_edges
        if bin_edges is None:
            bin_edges = self.define_bin_edges(x, weights=weights, cache=False)

        density = self.stat == "density"
        hist, _ = np.histogram(
            x, bin_edges, weights=weights, density=density,
        )

        if self.stat == "probability":
            hist = hist.astype(float) / hist.sum()

        if self.cumulative:
            if density:
                hist = (hist * np.diff(bin_edges)).cumsum()
            else:
                hist = hist.cumsum()

        return hist, bin_edges

    def __call__(self, x1, x2=None, weights=None):
        """Count the occurrances in each bin, maybe normalize."""
        if x2 is None:
            return self._eval_univariate(x1, weights)
        else:
            return self._eval_bivariate(x1, x2, weights)
