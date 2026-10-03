import { BrowserRouter, Route, Routes } from 'react-router'
import { Layout } from './components/Layout'
import { ChatPage } from './pages/Chat'
import { HistoryList, TranscriptPage } from './pages/History'
import { Home } from './pages/Home'
import { NewConversation } from './pages/NewConversation'
import { ProgressPage } from './pages/ProgressPage'
import { ConversationsProvider } from './state/conversations'

export default function App() {
  return (
    <ConversationsProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Home />} />
            <Route path="new" element={<NewConversation />} />
            <Route path="chat/:sessionId" element={<ChatPage />} />
            <Route path="history" element={<HistoryList />} />
            <Route path="history/:sessionId" element={<TranscriptPage />} />
            <Route path="progress" element={<ProgressPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ConversationsProvider>
  )
}
