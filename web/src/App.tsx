import { BrowserRouter, Route, Routes } from 'react-router'
import { Layout } from './components/Layout'
import { ChatPage } from './pages/Chat'
import { HistoryList, TranscriptPage } from './pages/History'
import { Home } from './pages/Home'
import { NewConversation } from './pages/NewConversation'
import { ProgressPage } from './pages/ProgressPage'
import { RatePage } from './pages/Rate'
import { Books } from './pages/Books'
import { ReadingPage } from './pages/ReadingPage'
import { Songs } from './pages/Songs'
import { Stories } from './pages/Stories'
import { WhatNext } from './pages/WhatNext'
import { ConversationsProvider } from './state/conversations'
import { SpeechProvider } from './state/speech'

export default function App() {
  return (
    <SpeechProvider>
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
              <Route path="rate" element={<RatePage />} />
              <Route path="next" element={<WhatNext />} />
              <Route path="reading/:sessionId" element={<ReadingPage />} />
              <Route path="books" element={<Books />} />
              <Route path="stories" element={<Stories />} />
              <Route path="songs" element={<Songs />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ConversationsProvider>
    </SpeechProvider>
  )
}
