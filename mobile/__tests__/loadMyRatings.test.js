import AsyncStorage from '@react-native-async-storage/async-storage';
import { loadMyRatings, RATINGS_KEY } from '../screens/MyRatingsScreen';

beforeEach(async () => {
  await AsyncStorage.clear();
});

test('returns stored ratings when present', async () => {
  await AsyncStorage.setItem(RATINGS_KEY, JSON.stringify({ uscfRating: '1500', fideRating: '1450' }));
  const result = await loadMyRatings();
  expect(result).toEqual({ uscfRating: '1500', fideRating: '1450' });
});

test('returns a blank profile when nothing is stored', async () => {
  const result = await loadMyRatings();
  expect(result).toEqual({ uscfRating: '', fideRating: '' });
});

test('returns a blank profile when the stored value is corrupt JSON', async () => {
  await AsyncStorage.setItem(RATINGS_KEY, 'not valid json{{{');
  const result = await loadMyRatings();
  expect(result).toEqual({ uscfRating: '', fideRating: '' });
});
