import Stack from './RootStack';
import Tabs from 'nav/Tabs';
import getJourneyScreens from './journeyScreens';
import Checkout from './Checkout';
import { Home } from 'screens/Home';
import CustomTabs from './CustomTabs';
const withLoading = (C: any) => C; const observer = (C: any) => C;
const RootNavigator = ({ loggedIn }: any) => (
  <Stack.Navigator>
    {loggedIn ? <Stack.Screen name="Tabs" component={Tabs} /> : <Stack.Screen name="Home" component={Home} />}
    <Stack.Screen name="Custom" getComponent={() => CustomTabs} />
    {getJourneyScreens({ Flow: Checkout })}
  </Stack.Navigator>
);
export default withLoading(observer(RootNavigator));
