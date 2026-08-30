/// Null-safe JSON coercions shared by every `fromJson`.
///
/// Phase 5 of the backend is not built, so the response shape is not frozen.
/// Every reader here tolerates a missing key, an explicit null, or a value of
/// the wrong type, and returns a documented fallback instead of throwing.
library;

/// Reads [key] as a nested JSON object, or null.
Map<String, dynamic>? asMap(Object? json, String key) {
  if (json is! Map) return null;
  final Object? value = json[key];
  if (value is Map<String, dynamic>) return value;
  if (value is Map) return value.cast<String, dynamic>();
  return null;
}

/// Reads [key] as a list of dynamic entries, or an empty list.
List<dynamic> asList(Object? json, String key) {
  if (json is! Map) return const <dynamic>[];
  final Object? value = json[key];
  return value is List ? value : const <dynamic>[];
}

/// Reads [key] as a list of maps, skipping entries that are not maps.
List<Map<String, dynamic>> asMapList(Object? json, String key) {
  return asList(json, key)
      .whereType<Map>()
      .map((Map<dynamic, dynamic> e) => e.cast<String, dynamic>())
      .toList();
}

/// Reads [key] as a string, or null. Numbers are stringified.
String? asStringOrNull(Object? json, String key) {
  if (json is! Map) return null;
  final Object? value = json[key];
  if (value is String) return value;
  if (value is num) return value.toString();
  return null;
}

/// Reads [key] as a string, falling back to [fallback].
String asString(Object? json, String key, {String fallback = ''}) =>
    asStringOrNull(json, key) ?? fallback;

/// Reads [key] as a list of strings, dropping non-string entries.
List<String> asStringList(Object? json, String key) =>
    asList(json, key).whereType<String>().toList();

/// Reads [key] as a double, or null. Accepts an int or a numeric string.
double? asDoubleOrNull(Object? json, String key) {
  if (json is! Map) return null;
  final Object? value = json[key];
  if (value is num) return value.toDouble();
  if (value is String) return double.tryParse(value);
  return null;
}

/// Reads [key] as an int, or null.
///
/// A non-integral double is rejected rather than truncated: `ball_speed_mph` is
/// a StrictInt server-side and a decimal there means a contract violation, not
/// a value to round.
int? asIntOrNull(Object? json, String key) {
  if (json is! Map) return null;
  final Object? value = json[key];
  if (value is int) return value;
  if (value is double) return value == value.roundToDouble() ? value.toInt() : null;
  if (value is String) return int.tryParse(value);
  return null;
}

/// Reads [key] as an int, falling back to [fallback].
int asInt(Object? json, String key, {int fallback = 0}) =>
    asIntOrNull(json, key) ?? fallback;

/// Reads [key] as a bool, falling back to [fallback].
bool asBool(Object? json, String key, {bool fallback = false}) {
  if (json is! Map) return fallback;
  final Object? value = json[key];
  return value is bool ? value : fallback;
}

/// Reads [key] as an ISO-8601 timestamp, or null on anything unparseable.
DateTime? asDateTimeOrNull(Object? json, String key) {
  final String? raw = asStringOrNull(json, key);
  if (raw == null) return null;
  return DateTime.tryParse(raw)?.toLocal();
}
