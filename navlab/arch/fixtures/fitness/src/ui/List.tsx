import {ScrollView, Text} from 'react-native'
import {bus} from '../sync/bus'
import {CONVO_KEY} from '../store/convo'
export function List({qc, items}: any) {
  bus.on(null)
  qc.setQueryData(CONVO_KEY('x'), {})
  return (
    <ScrollView>
      {items.map((i: string) => <Text key={i}>{i}</Text>)}
    </ScrollView>
  )
}
