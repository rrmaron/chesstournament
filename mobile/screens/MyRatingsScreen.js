import React, { useState, useCallback } from 'react';
import {
  View, Text, TextInput, TouchableOpacity,
  StyleSheet, ScrollView, KeyboardAvoidingView, Platform,
  ActivityIndicator, Linking,
} from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useFocusEffect } from '@react-navigation/native';

const API_BASE = 'https://mychessrating.fly.dev';
export const RATINGS_KEY = 'myRatings';

export async function loadMyRatings() {
  try {
    const raw = await AsyncStorage.getItem(RATINGS_KEY);
    return raw ? JSON.parse(raw) : { uscfRating: '', fideRating: '' };
  } catch {
    return { uscfRating: '', fideRating: '' };
  }
}

export default function MyRatingsScreen() {
  const [profile, setProfile] = useState(null); // { uscfId, name, uscfRating, fideRating }
  const [refreshing, setRefreshing] = useState(false);
  const [uscfRating, setUscfRating] = useState('');
  const [fideRating, setFideRating] = useState('');
  const [saved, setSaved] = useState(false);

  const fetchLiveRatings = useCallback(async (uscfId, name) => {
    setRefreshing(true);
    try {
      const [statusRes, detailRes] = await Promise.all([
        fetch(`${API_BASE}/api/uscf-player-status?uscf_id=${encodeURIComponent(uscfId)}`),
        fetch(`${API_BASE}/api/public/player-details?uscf_id=${encodeURIComponent(uscfId)}`),
      ]);
      const [statusData, detailData] = await Promise.all([statusRes.json(), detailRes.json()]);
      const liveUscf = statusData.live_rating || statusData.rating || 0;
      const liveFide = detailData.fide_rating || 0;
      const updated = { uscfId, name, uscfRating: liveUscf, fideRating: liveFide };
      await AsyncStorage.setItem(RATINGS_KEY, JSON.stringify(updated));
      setProfile(updated);
      setUscfRating(String(liveUscf || ''));
      setFideRating(String(liveFide || ''));
    } catch {}
    setRefreshing(false);
  }, []);

  useFocusEffect(
    useCallback(() => {
      loadMyRatings().then((data) => {
        setUscfRating(String(data.uscfRating || ''));
        setFideRating(String(data.fideRating || ''));
        if (data.uscfId && data.name) {
          setProfile(data);
          fetchLiveRatings(data.uscfId, data.name);
        } else {
          setProfile(null);
        }
      });
    }, [fetchLiveRatings])
  );

  const clearProfile = async () => {
    await AsyncStorage.setItem(RATINGS_KEY, JSON.stringify({ uscfRating: 0, fideRating: 0 }));
    setProfile(null);
    setUscfRating('');
    setFideRating('');
  };

  const handleSave = async () => {
    const updated = {
      uscfRating: Number(uscfRating) || 0,
      fideRating: Number(fideRating) || 0,
      ...(profile ? { uscfId: profile.uscfId, name: profile.name } : {}),
    };
    await AsyncStorage.setItem(RATINGS_KEY, JSON.stringify(updated));
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  return (
    <KeyboardAvoidingView
      style={{ flex: 1 }}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView style={styles.container} contentContainerStyle={styles.content}>

        {profile?.uscfId ? (
          /* ── Linked player card ── */
          <View style={styles.profileCard}>
            <View style={styles.profileHeader}>
              <View style={{ flex: 1 }}>
                <Text style={styles.profileName}>{profile.name}</Text>
                <TouchableOpacity
                  onPress={() => Linking.openURL(
                    `https://ratings.uschess.org/player/${profile.uscfId}`
                  )}
                >
                  <Text style={styles.profileId}>USCF ID: {profile.uscfId} ↗</Text>
                </TouchableOpacity>
              </View>
              <TouchableOpacity onPress={clearProfile} style={styles.clearBtn}>
                <Text style={styles.clearBtnText}>Clear</Text>
              </TouchableOpacity>
            </View>

            <View style={styles.ratingRow}>
              <View style={styles.ratingItem}>
                <Text style={styles.ratingLabel}>Live USCF</Text>
                <Text style={styles.ratingValue}>
                  {refreshing ? '…' : (profile.uscfRating || '—')}
                </Text>
              </View>
              {profile.fideRating ? (
                <View style={styles.ratingItem}>
                  <Text style={styles.ratingLabel}>FIDE</Text>
                  <Text style={styles.ratingValue}>
                    {refreshing ? '…' : profile.fideRating}
                  </Text>
                </View>
              ) : null}
            </View>

            <TouchableOpacity
              style={styles.refreshBtn}
              onPress={() => fetchLiveRatings(profile.uscfId, profile.name)}
              disabled={refreshing}
            >
              {refreshing
                ? <ActivityIndicator size="small" color="rgba(255,255,255,0.7)" />
                : <Text style={styles.refreshBtnText}>↻  Refresh live ratings</Text>
              }
            </TouchableOpacity>
          </View>
        ) : (
          <Text style={styles.intro}>
            Enter your ratings below, or tap "Use as my profile" on any player's page to link your USCF account.
          </Text>
        )}

        {/* Manual edit / override */}
        <View style={styles.card}>
          <View style={styles.field}>
            <Text style={styles.label}>My Live USCF Rating</Text>
            <TextInput
              style={styles.input}
              value={uscfRating}
              onChangeText={(t) => { setUscfRating(t); setSaved(false); }}
              placeholder="e.g. 1650"
              keyboardType="number-pad"
              maxLength={4}
            />
          </View>
          <View style={[styles.field, { borderBottomWidth: 0 }]}>
            <Text style={styles.label}>My FIDE Rating</Text>
            <TextInput
              style={styles.input}
              value={fideRating}
              onChangeText={(t) => { setFideRating(t); setSaved(false); }}
              placeholder="e.g. 1600"
              keyboardType="number-pad"
              maxLength={4}
            />
          </View>
        </View>

        <TouchableOpacity style={styles.saveBtn} onPress={handleSave}>
          <Text style={styles.saveBtnText}>{saved ? '✓ Saved' : 'Save'}</Text>
        </TouchableOpacity>

        <Text style={styles.hint}>
          {profile?.uscfId
            ? 'Ratings sync from USCF automatically. Edit above to override.'
            : 'Ratings are stored only on this device.'}
        </Text>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f5f5' },
  content: { padding: 16, paddingBottom: 40 },
  intro: { fontSize: 14, color: '#555', marginBottom: 16, lineHeight: 20 },

  /* Linked player card */
  profileCard: {
    backgroundColor: '#1a1a2e',
    borderRadius: 12,
    padding: 16,
    marginBottom: 16,
  },
  profileHeader: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginBottom: 14,
  },
  profileName: {
    color: '#fff',
    fontSize: 18,
    fontWeight: '700',
    marginBottom: 4,
  },
  profileId: {
    color: 'rgba(255,255,255,0.55)',
    fontSize: 12,
    textDecorationLine: 'underline',
  },
  clearBtn: {
    backgroundColor: 'rgba(255,255,255,0.12)',
    borderRadius: 6,
    paddingHorizontal: 12,
    paddingVertical: 6,
    marginLeft: 8,
  },
  clearBtnText: { color: '#fff', fontSize: 12, fontWeight: '600' },
  ratingRow: { flexDirection: 'row', gap: 10, marginBottom: 12 },
  ratingItem: {
    flex: 1,
    backgroundColor: 'rgba(255,255,255,0.1)',
    borderRadius: 8,
    paddingVertical: 12,
    alignItems: 'center',
  },
  ratingLabel: {
    color: 'rgba(255,255,255,0.55)',
    fontSize: 10,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    marginBottom: 4,
  },
  ratingValue: { color: '#fff', fontSize: 26, fontWeight: '700' },
  refreshBtn: {
    backgroundColor: 'rgba(255,255,255,0.08)',
    borderRadius: 8,
    paddingVertical: 10,
    alignItems: 'center',
  },
  refreshBtnText: { color: 'rgba(255,255,255,0.7)', fontSize: 13, fontWeight: '500' },

  /* Manual edit card */
  card: {
    backgroundColor: '#fff',
    borderRadius: 12,
    overflow: 'hidden',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.08,
    shadowRadius: 4,
    elevation: 2,
    marginBottom: 16,
  },
  field: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#f0f0f0',
  },
  label: { fontSize: 15, color: '#333', fontWeight: '500' },
  input: {
    fontSize: 15,
    color: '#1a1a2e',
    fontWeight: '600',
    textAlign: 'right',
    minWidth: 80,
    padding: 4,
    borderBottomWidth: 1,
    borderBottomColor: '#ccc',
  },
  saveBtn: {
    backgroundColor: '#1a1a2e',
    borderRadius: 10,
    padding: 14,
    alignItems: 'center',
    marginBottom: 12,
  },
  saveBtnText: { color: '#fff', fontSize: 16, fontWeight: '600' },
  hint: { fontSize: 12, color: '#aaa', textAlign: 'center' },
});
