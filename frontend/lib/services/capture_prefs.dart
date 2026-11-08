import 'package:shared_preferences/shared_preferences.dart';

import '../models/enums.dart';

/// Remembers two capture settings between sessions: the racket hand and the
/// start delay.
///
/// Why the racket hand can be remembered without breaking the NO-DEFAULT rule
/// on the record screen: that rule exists because a *defaulted* hint is
/// indistinguishable from a confirmed one. A remembered hand is not a default.
/// It is the hand this player explicitly chose last time. A player's racket
/// hand does not change between sessions, and making them re-pick it before
/// every swing was pure friction. A first-time user still starts with nothing
/// selected and must choose.

/// The `shared_preferences` keys.
const String kRacketHandPrefsKey = 'racket_hand';
const String kStartDelayPrefsKey = 'start_delay_seconds';

/// The start-delay choices, in seconds. 0 means "start recording on press".
const List<int> kStartDelayChoices = <int>[0, 5, 10];

/// PURE. The stored racket hand, or null when nothing usable is stored.
/// `unknown` is never a choice the user can make, so it decodes to null too.
Handedness? decodeRacketHand(String? raw) {
  if (raw == Handedness.right.wire) return Handedness.right;
  if (raw == Handedness.left.wire) return Handedness.left;
  return null;
}

/// PURE. The stored start delay, or 0 for anything not offered in
/// [kStartDelayChoices]. A corrupted value can never become a 30 s wait.
int decodeStartDelay(int? raw) =>
    raw != null && kStartDelayChoices.contains(raw) ? raw : 0;

/// Where capture settings are written down. Every method may throw. The
/// record screen treats a failure as "nothing remembered", never as an error.
abstract interface class CapturePrefsStore {
  Future<Handedness?> readRacketHand();
  Future<void> writeRacketHand(Handedness? hand);
  Future<int> readStartDelay();
  Future<void> writeStartDelay(int seconds);
}

/// [CapturePrefsStore] on top of `shared_preferences`.
class PreferencesCapturePrefsStore implements CapturePrefsStore {
  const PreferencesCapturePrefsStore();

  @override
  Future<Handedness?> readRacketHand() async {
    final SharedPreferences prefs = await SharedPreferences.getInstance();
    return decodeRacketHand(prefs.getString(kRacketHandPrefsKey));
  }

  @override
  Future<void> writeRacketHand(Handedness? hand) async {
    final SharedPreferences prefs = await SharedPreferences.getInstance();
    if (hand == null || hand == Handedness.unknown) {
      await prefs.remove(kRacketHandPrefsKey);
    } else {
      await prefs.setString(kRacketHandPrefsKey, hand.wire);
    }
  }

  @override
  Future<int> readStartDelay() async {
    final SharedPreferences prefs = await SharedPreferences.getInstance();
    return decodeStartDelay(prefs.getInt(kStartDelayPrefsKey));
  }

  @override
  Future<void> writeStartDelay(int seconds) async {
    final SharedPreferences prefs = await SharedPreferences.getInstance();
    await prefs.setInt(kStartDelayPrefsKey, decodeStartDelay(seconds));
  }
}
