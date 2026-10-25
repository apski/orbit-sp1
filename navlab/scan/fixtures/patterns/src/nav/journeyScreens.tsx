import Stack from './RootStack';
const getJourneyScreens = (options: any) => (
  <Stack.Group><Stack.Screen name="Checkout" component={options.Flow} /></Stack.Group>
);
export default getJourneyScreens;
