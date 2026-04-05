import React, { useState, useEffect } from 'react';
import {
  View, Text, TextInput, TouchableOpacity,
  StyleSheet, ScrollView, KeyboardAvoidingView, Platform,
} from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

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
  const [uscfRating, setUscfRating] = useState('');
  const [fideRating, setFideRating] = useState('');
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    loadMyRatings().then(({ uscfRating, fideRating }) => {
      setUscfRating(String(uscfRating || ''));
      setFideRating(String(fideRating || ''));
    });
  }, []);

  const handleSave = async () => {
    await AsyncStorage.setItem(
      RATINGS_KEY,
      JSON.stringify({ uscfRating: Number(uscfRating) || 0, fideRating: Number(fideRating) || 0 })
    );
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  return (
    <KeyboardAvoidingView
      style={{ flex: 1 }}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView style={styles.container} contentContainerStyle={styles.content}>
        <Text style={styles.intro}>
          Enter your current ratings so the app can show how any game would affect your score.
        </Text>

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
          Leave a field blank if you don't have that rating. Ratings are stored only on this device.
        </Text>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f5f5' },
  content: { padding: 16, paddingBottom: 40 },
  intro: { fontSize: 14, color: '#555', marginBottom: 16, lineHeight: 20 },
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
