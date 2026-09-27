/// Which clips may be compared with which: the pipeline-version rule.
///
/// PURE, like the rest of `models/`: lists in, indices out, no I/O.
///
/// When the backend changes how a swing is measured it bumps
/// `pipeline_version` (v2 -> v3 fixed the swing-path angle for leftward
/// swings). A score from one version and a score from another are different
/// measurements, so a "+40, Improving" across that line would be a trend the
/// player did not earn. The rule applied everywhere on the dashboard:
///
/// * **History stays visible.** A line chart keeps every point and BREAKS
///   the line where the version changes, with a "Scoring updated" marker.
/// * **Every comparison stays inside one version** — the version of the
///   newest clip. A delta, a direction, an average, a best, a spread or a
///   "previous swing" never reaches across the line. When that leaves too
///   few clips, the existing "need 2 for a trend" wording is shown instead.
///
/// The version is read from the HISTORY LIST item, never from a fetched
/// detail, so every series on the screen is segmented by one source.
///
/// ## `null`
///
/// A backend deployed before the list carried `pipeline_version` sends none.
/// `null` is then one "unknown" version like any other string: a history that
/// is all-null has no boundary and behaves exactly as before. A null beside a
/// known version IS a boundary — nothing shows the two to be comparable, and
/// the rule errs towards not comparing.
///
/// ## What the version does not gate
///
/// Ball speed. It is a physical measurement from the ball track and the
/// player's two calibration taps, not a rubric score, and the v3 change
/// touched only the swing-path fold. Its sparkline and the "top speed"
/// record therefore ignore the version. A future version that changes the
/// speed maths would have to revisit this.
library;

/// The version at [index] of [versions], or null when [versions] is shorter.
///
/// A series built without versions (an empty list) therefore reads as
/// all-null: one unknown version, no boundary.
String? versionAt(List<String?> versions, int index) =>
    index >= 0 && index < versions.length ? versions[index] : null;

/// The indices, in a series of [length] clips ordered OLDEST FIRST, where the
/// version differs from the clip before it.
///
/// Index `i` in the result means "the line breaks between clip `i - 1` and
/// clip `i`". Empty when every clip shares one version (all-null included).
List<int> versionBoundariesOf(List<String?> versions, int length) => <int>[
  for (int i = 1; i < length; i++)
    if (versionAt(versions, i) != versionAt(versions, i - 1)) i,
];

/// The indices, in a series of [length] clips ordered OLDEST FIRST, whose
/// version matches the NEWEST clip's — the only clips a comparison may use.
List<int> comparableIndices(List<String?> versions, int length) {
  if (length <= 0) return const <int>[];
  final String? latest = versionAt(versions, length - 1);
  return <int>[
    for (int i = 0; i < length; i++)
      if (versionAt(versions, i) == latest) i,
  ];
}

/// [values] (OLDEST FIRST) cut down to the clips comparable with the newest,
/// order kept. Nulls in [values] are kept in place: "not measured" is still
/// not zero.
List<T> comparableOnly<T>(List<T> values, List<String?> versions) => <T>[
  for (final int i in comparableIndices(versions, values.length)) values[i],
];
