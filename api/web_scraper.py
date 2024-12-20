import requests
from bs4 import BeautifulSoup


def process_newsletter_link(newsletter_link):
    # Step 1: Scrape the webpage
    response = requests.get(newsletter_link)
    soup = BeautifulSoup(response.text, 'html.parser')

    # Step 2: Extract text from the webpage
    text_content = ""
    for paragraph in soup.find_all('p'):
        text_content += paragraph.get_text() + "\n"
    return text_content
    # # Step 3: Summarize the text
    # parser = PlaintextParser.from_string(text_content, Tokenizer('english'))
    # summarizer = LsaSummarizer()
    # # Adjust the number of sentences in the summary
    # summary = summarizer(parser.document, 2)

    # return ' '.join(str(sentence) for sentence in summary)


if __name__ == '__main__':
    # Example usage
    newsletter_links = ['https://www.bdc.ca/en/articles-tools/money-finance/manage-finances/how-to-evaluate-capital-investment-questions-to-ask?utm_campaign=LN--MF--C--010&utm_medium=email&utm_source=Eloqua&elqcst=272&elqcsid=15645',
                        'https://www.bdc.ca/en/articles-tools/money-finance/manage-finances/how-to-evaluate-capital-investment-questions-to-ask?utm_campaign=LN--MF--C--010&utm_medium=email&utm_source=Eloqua&elqcst=272&elqcsid=15645']
    for link in newsletter_links:
        summarized_text = process_newsletter_link(link)
        print(summarized_text)
