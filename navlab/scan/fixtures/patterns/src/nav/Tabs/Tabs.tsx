import { createAppTabs } from '../factory';
import { Home } from 'screens/Home';
const Tab = createAppTabs();
const TABS = ['A', 'B', 'C', 'D', 'E'];
export default function Tabs() {
  return <Tab.Navigator>{TABS.map((n) => <Tab.Screen key={n} name={n} component={Home} />)}</Tab.Navigator>;
}
