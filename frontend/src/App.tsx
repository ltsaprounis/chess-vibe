import { Routes, Route } from 'react-router-dom'
import { Layout } from './components/Layout'
import { PlayPage } from './pages/PlayPage'
import { SPRTTestsPage } from './pages/SPRTTestsPage'
import { GameReplayPage } from './pages/GameReplayPage'

export function App(): React.JSX.Element {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<PlayPage />} />
        <Route path="sprt" element={<SPRTTestsPage />} />
        <Route path="games/:id?" element={<GameReplayPage />} />
      </Route>
    </Routes>
  )
}
