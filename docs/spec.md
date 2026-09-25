The task is to write a python script named jevq.py

The script calls the TypeSafe Jev API in one of three ways: -c, -p, or -q, using command line arguments

Examples:

Perform a choice-type call with options -c --choices and -q --question for command line text or piped in stdin:
jevq -c Heaven,Hell -q "Where should this one go?" Frank Sinatra

Perform a probability/noul type call (-p --probability) synonyms (-n --noul) for question -q given message text via stdin
jevq -p -q "Does this ask for a refund?" < message.txt

Perform a score type call (-s --score) with question -q
jevq -s -q "What is the user's hostility level" < message.txt

Return values are the relevant portion of the API return, or an error, in JSON format
- choice: return the scores and confidence
- noul: return the %true value
- score: the score

Assume TYPESAFE_API_KEY is set to the proper key.
