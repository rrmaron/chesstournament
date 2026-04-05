import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { StatusBar } from 'expo-status-bar';
import { TouchableOpacity, Text } from 'react-native';
import SearchScreen from './screens/SearchScreen';
import PlayerScreen from './screens/PlayerScreen';
import MyRatingsScreen from './screens/MyRatingsScreen';

const Stack = createNativeStackNavigator();

export default function App() {
  return (
    <NavigationContainer>
      <StatusBar style="light" />
      <Stack.Navigator
        screenOptions={{
          headerStyle: { backgroundColor: '#1a1a2e' },
          headerTintColor: '#fff',
          headerTitleStyle: { fontWeight: 'bold' },
        }}
      >
        <Stack.Screen
          name="Search"
          component={SearchScreen}
          options={({ navigation }) => ({
            title: '♟ Chess Player Lookup',
            headerRight: () => (
              <TouchableOpacity onPress={() => navigation.navigate('MyRatings')} style={{ marginRight: 4 }}>
                <Text style={{ color: '#fff', fontSize: 22 }}>⚙</Text>
              </TouchableOpacity>
            ),
          })}
        />
        <Stack.Screen
          name="Player"
          component={PlayerScreen}
          options={({ route }) => ({ title: route.params.name })}
        />
        <Stack.Screen
          name="MyRatings"
          component={MyRatingsScreen}
          options={{ title: 'My Ratings' }}
        />
      </Stack.Navigator>
    </NavigationContainer>
  );
}
