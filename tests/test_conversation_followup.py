from app.agent.ai_agent import AIAgent

agent = AIAgent()
business_id = 'ABC_Mobiles'

# Simulate initial assistant recommendation (we'll add it to memory)
agent.memory.add_message('assistant', 'I recommend ASUS Vivobook Pro as the best product for coding.')

queries = [
    'why it is best',
    'what is its price?',
    'how much RAM does it have?',
    'is it available?',
    'is it good for coding?'
]

for q in queries:
    print('\n=== User:', q)
    resp = agent.chat(business_id, q)
    print('\nAssistant Response:')
    print(resp)
